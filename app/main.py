import os
import json
import yaml
import httpx
import logging
import docker
import asyncio
import re
from datetime import datetime
from urllib.parse import quote, unquote, urlparse
from typing import List, Dict, Any, Optional
from collections import Counter
import pytz

# 检查依赖
try:
    import aiofiles
except ImportError:
    print("CRITICAL ERROR: 缺少 'aiofiles' 库。请确保 requirements.txt 中包含 aiofiles 并重新构建镜像！")
    exit(1)

from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# [Scheduler]
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# --- 配置路径 ---
DATA_PATH = "/data"
CONFIG_JSON = os.path.join(DATA_PATH, "config.json")
OUTPUT_YAML = os.path.join(DATA_PATH, "config.yaml")
DEFAULT_BACKEND = "https://api.v1.mk/sub?target=clash&url="

# --- 初始化日志 ---
LOG_FILE = os.path.join(DATA_PATH, "app.log")
logger = logging.getLogger("ClashWeb")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

if not os.path.exists(DATA_PATH):
    try:
        os.makedirs(DATA_PATH)
    except:
        pass

# 使用 utf-8 编码初始化日志文件处理器，防止中文乱码
file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# --- 初始化调度器 ---
tz = pytz.timezone('Asia/Shanghai')
scheduler = AsyncIOScheduler(timezone=tz)

app = FastAPI(title="ClashWeb")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 数据模型 ---

class UserInfo(BaseModel):
    name: str = "未订阅" 
    webUrl: str = "" 
    upload: int = 0
    download: int = 0
    total: int = 0
    expire: int = 0
    update_time: str = ""

class SubHistoryItem(BaseModel):
    url: str
    date: str
    name: Optional[str] = "未知机场"
    info: Optional[Dict[str, Any]] = {}
    remarks: Optional[str] = ""

class ConfigModel(BaseModel):
    sub_backend: Optional[str] = ""
    sub_url: Optional[str] = ""
    restart_containers: Optional[str] = "" 
    auto_update: Optional[bool] = False
    cron_expression: Optional[str] = "0 4 * * *" 
    user_info: Optional[UserInfo] = UserInfo()
    sub_history: Optional[List[SubHistoryItem]] = []
    add_groups: Optional[List[Dict[str, Any]]] = []
    del_groups: Optional[List[str]] = []
    add_rules: Optional[List[str]] = []
    del_rules: Optional[List[str]] = []

class DownloadRequest(BaseModel):
    url: str

# --- 核心工具函数 ---

def init_data():
    if not os.path.exists(DATA_PATH):
        try:
            os.makedirs(DATA_PATH)
        except: pass
    
    if not os.path.exists(CONFIG_JSON) or os.path.isdir(CONFIG_JSON):
        with open(CONFIG_JSON, 'w', encoding='utf-8') as f: json.dump(ConfigModel().dict(), f)

    if not os.path.exists(OUTPUT_YAML) or os.path.isdir(OUTPUT_YAML):
        with open(OUTPUT_YAML, 'w', encoding='utf-8') as f: f.write("")

def refresh_scheduler():
    try:
        with open(CONFIG_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        job_id = 'auto_update_job'
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        if data.get('auto_update') and data.get('cron_expression'):
            cron_str = data['cron_expression']
            try:
                # 尝试验证 Cron 表达式
                trigger = CronTrigger.from_crontab(cron_str, timezone=tz)
                scheduler.add_job(scheduled_update_task, trigger, id=job_id, replace_existing=True)
                logger.info(f"✅ 定时任务已设置: [{cron_str}]")
            except Exception as e:
                # 捕获无效表达式，防止程序崩溃
                logger.error(f"❌ Cron 表达式无效 '{cron_str}': {e}。定时任务未启动。建议检查表达式格式 (如 '0 4 * * *')")
        else:
            logger.info("⛔️ 定时任务已关闭")
    except Exception as e:
        logger.error(f"Scheduler refresh failed: {e}")

async def scheduled_update_task():
    logger.info(">>> ⏳ 开始执行定时更新任务 <<<")
    try:
        async with aiofiles.open(CONFIG_JSON, 'r', encoding='utf-8') as f:
            content = await f.read()
            data = json.loads(content)
        
        url = data.get('sub_url')
        if not url:
            logger.warning("未配置订阅链接，跳过更新")
            return

        # 执行更新逻辑
        await internal_process_subscription(url, data)
        
        # 保存 user_info 更新
        async with aiofiles.open(CONFIG_JSON, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=2))

        logger.info("✅ 定时更新任务完成")

        # 自动重启容器逻辑 (兼容中文逗号)
        container_str = data.get('restart_containers', '').replace('，', ',')
        if container_str:
            try:
                client = docker.from_env()
                targets = [name.strip() for name in container_str.split(',') if name.strip()]
                for name in targets:
                    try:
                        client.containers.get(name).restart()
                        logger.info(f"✅ (定时) 容器已重启: {name}")
                    except Exception as e:
                        logger.error(f"❌ (定时) 重启容器 {name} 失败: {e}")
            except Exception as e:
                logger.error(f"Docker 连接失败: {e}")
                    
    except Exception as e:
        logger.error(f"❌ 定时任务执行出错: {e}")

# --- 任务1: 获取原始流量信息 ---
async def fetch_original_userinfo(url: str) -> Optional[dict]:
    """直接请求原始订阅链接，提取 Header 中的流量信息、profile-title 和官网地址"""
    logger.info(f"📡 [信息获取] 正在请求原始链接: {url}")
    headers = {"User-Agent": "ClashForAndroid/2.5.12"} 
    
    try:
        async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
            # 使用 GET 但通过 stream 立即关闭，避免下载大文件
            async with client.stream("GET", url, headers=headers, timeout=30.0) as resp:
                
                # 1. 提取流量信息
                user_info_header = None
                for k, v in resp.headers.items():
                    if k.lower() == 'subscription-userinfo':
                        user_info_header = v
                        break
                
                info = {}
                if user_info_header:
                    parts = user_info_header.split(';')
                    for part in parts:
                        if '=' in part:
                            kv = part.strip().split('=')
                            if len(kv) >= 2:
                                info[kv[0].strip()] = int(kv[1].strip())

                # 2. 提取机场名称
                airport_name = ""
                # A. 优先检查 profile-title
                for k, v in resp.headers.items():
                    if k.lower() == 'profile-title':
                        try: airport_name = unquote(v)
                        except: airport_name = v
                        break
                
                # B. Content-Disposition 提取 (增强兼容性)
                if not airport_name:
                    for k, v in resp.headers.items():
                        if k.lower() == 'content-disposition':
                            m = re.search(r'filename\*?=(?:UTF-8\'\')?([^;]+)', v, re.IGNORECASE)
                            if m:
                                raw_name = m.group(1).strip('"\'')
                                try:
                                    airport_name = unquote(raw_name)
                                    if '.' in airport_name: airport_name = airport_name.rsplit('.', 1)[0]
                                except: pass
                            break
                
                # C. 域名兜底
                if not airport_name:
                    try: airport_name = urlparse(url).netloc
                    except: airport_name = "未知订阅"

                # 3. 提取官网地址 (webUrl)
                web_url = ""
                # A. 尝试从响应头获取 (Clash 标准头)
                for k, v in resp.headers.items():
                    if k.lower() == 'profile-web-page-url':
                        web_url = v.strip()
                        break
                
                # B. 域名兜底：如果头信息没有，使用 subscription url 的 root domain
                if not web_url:
                    try:
                        parsed = urlparse(url)
                        web_url = f"{parsed.scheme}://{parsed.netloc}"
                    except: pass

                result = {
                    "name": airport_name,
                    "webUrl": web_url,
                    "upload": info.get("upload", 0),
                    "download": info.get("download", 0),
                    "total": info.get("total", 0),
                    "expire": info.get("expire", 0),
                    "update_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                logger.info(f"✅ [信息获取] 成功: {result}")
                return result

    except Exception as e:
        logger.warning(f"❌ [信息获取] 请求失败: {e}")
        return None

# --- 任务2: 下载并转换配置 ---
async def download_and_convert_config(url: str, data: dict) -> bool:
    """请求转换后端，下载 YAML，并应用 Patch"""
    base_url = data.get('sub_backend') or DEFAULT_BACKEND
    if "target=" not in base_url:
        if not base_url.endswith("/"): base_url += "/"
        base_url += "sub?target=clash&url="
    
    encoded_sub_url = quote(url, safe='') 
    full_url = f"{base_url}{encoded_sub_url}"
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    logger.info(f"⬇️ [下载任务] 正在请求转换后端: {full_url}")
    
    config_yaml = ""
    try:
        async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
            resp = await client.get(full_url, headers=headers, timeout=60.0)
            if resp.status_code != 200:
                raise Exception(f"转换后端返回错误码: {resp.status_code}")
            
            config_yaml = resp.content.decode('utf-8', errors='ignore')
            if "No nodes were found" in config_yaml:
                raise Exception("后端返回 'No nodes were found'，请检查订阅链接是否有效")
    except Exception as e:
        logger.error(f"❌ [下载任务] 下载失败: {e}")
        raise e

    # 解析 YAML
    try:
        config = yaml.safe_load(config_yaml)
        if not isinstance(config, dict):
            raise Exception("解析结果不是字典格式")
    except Exception as e:
        logger.error(f"❌ [下载任务] YAML 解析失败: {e}")
        raise Exception("YAML 解析失败，内容可能不是有效的 Clash 配置")

    # 应用补丁 (Patch)
    try:
        final_config = apply_patch(config, data)
        # 强制允许 Unicode，防止中文乱码
        output_str = yaml.dump(final_config, allow_unicode=True, sort_keys=False, default_flow_style=False, width=float("inf"))
        yaml.safe_load(output_str) # 校验
    except Exception as e:
        logger.error(f"❌ [下载任务] 配置处理或校验失败: {e}")
        raise Exception(f"配置处理失败: {e}")

    # 写入文件
    async with aiofiles.open(OUTPUT_YAML, 'w', encoding='utf-8') as f:
        await f.write(output_str)
    
    logger.info("✅ [下载任务] 配置文件已生成 config.yaml")
    return True

# --- 主流程 ---
async def internal_process_subscription(url: str, data: dict) -> Optional[dict]:
    """
    并发执行：1.获取流量 2.下载配置
    """
    task_traffic = fetch_original_userinfo(url)
    task_download = download_and_convert_config(url, data)
    
    results = await asyncio.gather(task_traffic, task_download, return_exceptions=True)
    
    fetched_user_info = results[0]
    download_result = results[1]
    
    # 处理流量信息结果
    if isinstance(fetched_user_info, dict):
        data['user_info'] = fetched_user_info
    elif isinstance(fetched_user_info, Exception):
        logger.warning(f"流量信息获取任务异常: {fetched_user_info}")

    # 处理下载结果
    if isinstance(download_result, Exception):
        raise download_result

    return fetched_user_info if isinstance(fetched_user_info, dict) else None

def get_rule_target(rule_str: str) -> str:
    try:
        clean = rule_str.split('#')[0].strip()
        parts = clean.split(',')
        if len(parts) >= 3:
            return parts[2].strip()
    except: pass
    return ""

def clean_rule_for_clash(rule_str: str) -> str:
    # 简单的清理，主要用于比对
    return rule_str.split('#')[0].strip()

# --- Patch 逻辑 (关键修正：确保自定义内容生效) ---
def apply_patch(config: dict, patch: dict) -> dict:
    config['allow-lan'] = True
    config['external-controller'] = '0.0.0.0:9090'
    if 'bind-address' in config: config['bind-address'] = '*'

    # 确定参考节点 (用于新组默认填充)
    reference_proxies = ["DIRECT", "REJECT"]
    source_groups = config.get('proxy-groups', [])
    for g in source_groups:
        if g.get('type') == 'select' and len(g.get('proxies', [])) > 3:
            reference_proxies = g['proxies']
            break

    # [删除组逻辑]：使用 strip() 确保精准匹配
    del_groups_list = [n.strip() for n in (patch.get('del_groups') or []) if n.strip()]
    add_rules_raw = patch.get('add_rules') or []

    if del_groups_list:
        # 过滤组
        config['proxy-groups'] = [
            g for g in config.get('proxy-groups', []) 
            if g['name'].strip() not in del_groups_list
        ]
        
        # 级联删除：如果规则指向了已删除的组，则该规则也删除
        new_base_rules = []
        for rule in config.get('rules', []):
            target = get_rule_target(rule)
            if target not in del_groups_list:
                new_base_rules.append(rule)
        config['rules'] = new_base_rules
        
        # 同样过滤用户新增的规则
        valid_add_rules = []
        for rule in add_rules_raw:
            target = get_rule_target(rule)
            if target not in del_groups_list:
                valid_add_rules.append(rule)
        add_rules_raw = valid_add_rules

    # [添加组逻辑]：插入到最前
    add_groups = patch.get('add_groups') or []
    if add_groups:
        existing_names = {g['name'] for g in config.get('proxy-groups', [])}
        for g in reversed(add_groups):
            if g.get('name') and g['name'] not in existing_names:
                new_group = g.copy()
                current_proxies = new_group.get('proxies', [])
                if not current_proxies or current_proxies == ["DIRECT", "REJECT"]:
                     new_group['proxies'] = list(reference_proxies)
                config.setdefault('proxy-groups', []).insert(0, new_group)

    # [删除规则逻辑]：关键字过滤
    del_keywords = [k.strip() for k in (patch.get('del_rules') or []) if k.strip()]
    if del_keywords:
        final_rules = []
        for rule in config.get('rules', []):
            clean_rule = clean_rule_for_clash(rule)
            # 如果规则包含任何一个删除关键字，则丢弃
            if not any(k in clean_rule for k in del_keywords): 
                final_rules.append(rule)
        config['rules'] = final_rules

    # [添加规则逻辑]：强制插入到最前
    # 修复：直接插入，不过度清洗，保留备注
    if add_rules_raw:
        for r in reversed(add_rules_raw): 
            if r and r.strip():
                config.setdefault('rules', []).insert(0, r.strip())
             
    return config

@app.on_event("startup")
async def startup_event():
    init_data()
    scheduler.start()
    refresh_scheduler()
    logger.info("Application started, scheduler running.")

@app.get("/api/logs")
async def get_logs(lines: int = 100):
    if not os.path.exists(LOG_FILE):
        return {"logs": []}
    try:
        async with aiofiles.open(LOG_FILE, 'r', encoding='utf-8') as f:
            content = await f.read()
            all_lines = content.splitlines()
            return {"logs": all_lines[-lines:]}
    except Exception as e:
        return {"logs": [f"Error reading logs: {str(e)}"]}

@app.get("/api/data")
async def get_data():
    try:
        if os.path.exists(CONFIG_JSON) and os.path.getsize(CONFIG_JSON) > 0:
            async with aiofiles.open(CONFIG_JSON, 'r', encoding='utf-8') as f:
                content = await f.read()
                data = json.loads(content)
                if 'user_info' not in data:
                    data['user_info'] = UserInfo().dict()
                else:
                    # 补全可能缺失的字段
                    default_info = UserInfo().dict()
                    for k, v in default_info.items():
                        if k not in data['user_info']:
                            data['user_info'][k] = v
                return data
        return {}
    except: return {}

@app.post("/api/data")
async def save_data(data: ConfigModel):
    try:
        payload = data.dict(exclude_none=True)
        async with aiofiles.open(CONFIG_JSON, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(payload, indent=2))
        
        refresh_scheduler()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"保存配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# [清空订阅接口]
@app.delete("/api/subscription")
async def delete_subscription():
    try:
        async with aiofiles.open(CONFIG_JSON, 'r', encoding='utf-8') as f:
            content = await f.read()
            data = json.loads(content)
        
        # 清空
        data['sub_url'] = ""
        data['user_info'] = UserInfo().dict()
        
        async with aiofiles.open(CONFIG_JSON, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=2))
            
        # 重置 YAML
        minimal_config = {
            "port": 7890,
            "socks-port": 7891,
            "allow-lan": True,
            "mode": "Rule",
            "log-level": "info",
            "external-controller": "0.0.0.0:9090",
            "proxies": [],
            "proxy-groups": [],
            "rules": []
        }
        async with aiofiles.open(OUTPUT_YAML, 'w', encoding='utf-8') as f:
            await f.write(yaml.dump(minimal_config))
            
        logger.info("🗑️ 已清空当前订阅及配置")
        return {"status": "success", "msg": "订阅已清空"}
    except Exception as e:
        logger.error(f"清空订阅失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/backup")
async def backup_config(include_history: bool = False):
    if not os.path.exists(CONFIG_JSON): raise HTTPException(status_code=404, detail="No config found")
    try:
        async with aiofiles.open(CONFIG_JSON, 'r', encoding='utf-8') as f:
            content = await f.read()
            data = json.loads(content)
            
        if not include_history:
            # 清除订阅敏感信息
            data['sub_url'] = ""
            data['sub_history'] = []
            
        temp_path = "/tmp/clashweb_backup.json"
        async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=2))
            
        return FileResponse(temp_path, filename="clashweb_backup.json", media_type="application/json")
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.post("/api/restore")
async def restore_config(file: UploadFile = File(...)):
    try:
        content = await file.read()
        backup_data = json.loads(content)
        if not isinstance(backup_data, dict): raise ValueError("Format Error")
        
        current_data = {}
        if os.path.exists(CONFIG_JSON):
            with open(CONFIG_JSON, 'r', encoding='utf-8') as f: current_data = json.load(f)
        
        # 保护逻辑：如果备份没订阅，保留当前的
        if not backup_data.get('sub_url'):
            backup_data['sub_url'] = current_data.get('sub_url', "")
            if not backup_data.get('sub_history'):
                backup_data['sub_history'] = current_data.get('sub_history', [])
        
        merged_data = ConfigModel(**current_data).dict()
        merged_data.update(backup_data)

        async with aiofiles.open(CONFIG_JSON, "w", encoding='utf-8') as f:
            await f.write(json.dumps(merged_data, indent=2))
        
        refresh_scheduler()
        
        summary = {
            "groups": len(merged_data.get('add_groups', [])),
            "rules": len(merged_data.get('add_rules', [])),
            "has_sub": bool(merged_data.get('sub_url'))
        }
        return {"status": "success", "summary": summary}
    except Exception as e:
        logger.error(f"还原失败: {e}")
        raise HTTPException(status_code=400, detail=f"Restore Failed: {str(e)}")

@app.post("/api/restart_containers")
async def restart_containers():
    try:
        async with aiofiles.open(CONFIG_JSON, 'r', encoding='utf-8') as f:
            content = await f.read()
            data = json.loads(content)
        
        # [修复] 兼容中文逗号
        container_str = data.get('restart_containers', '').replace('，', ',')
        targets = [n.strip() for n in container_str.split(',') if n.strip()]

        if not targets: raise HTTPException(400, detail="未设置容器")
        
        client = docker.from_env()
        restarted = []
        for name in targets:
            try:
                client.containers.get(name).restart()
                restarted.append(name)
                logger.info(f"手动触发 - 容器已重启: {name}")
            except Exception as e:
                logger.error(f"手动触发 - 重启失败 {name}: {e}")
            
        if not restarted:
            raise HTTPException(status_code=404, detail="未找到有效容器")
            
        return {"status": "success", "msg": f"已重启: {', '.join(restarted)}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Docker Error: {str(e)}")

@app.post("/api/download")
async def download_config(req: DownloadRequest):
    if not req.url: raise HTTPException(status_code=400, detail="Missing URL")

    try:
        async with aiofiles.open(CONFIG_JSON, 'r', encoding='utf-8') as f:
            content = await f.read()
            data = json.loads(content)
    except: data = {}
    
    existing_history_entry = next((h for h in data.get('sub_history', []) if h.get('url') == req.url), None)
    data['sub_url'] = req.url
    
    try:
        # 获取流量 & 下载
        fetched_info = await internal_process_subscription(req.url, data)
        
        # 历史记录逻辑
        airport_name = existing_history_entry.get("name", "未知机场") if existing_history_entry else "未知机场"
        traffic_info = existing_history_entry.get("info", {}) if existing_history_entry else {}
        
        if fetched_info:
            airport_name = fetched_info.get("name", "未知机场")
            traffic_info = {
                "upload": fetched_info.get("upload", 0),
                "download": fetched_info.get("download", 0),
                "total": fetched_info.get("total", 0),
                "expire": fetched_info.get("expire", 0)
            }
        
        history = data.get('sub_history', [])
        history = [h for h in history if h.get('url') != req.url]
        
        new_record = {
            "url": req.url,
            "date": datetime.now().strftime('%Y-%m-%d %H:%M'),
            "name": airport_name,
            "info": traffic_info
        }
        history.insert(0, new_record)
        
        if len(history) > 10: history = history[:10]
        data['sub_history'] = history
        
        async with aiofiles.open(CONFIG_JSON, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=2))
            
    except Exception as e:
        logger.error(f"处理订阅出错: {e}")
        # 出错也要保存 URL
        async with aiofiles.open(CONFIG_JSON, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=2))
        raise HTTPException(status_code=500, detail=f"Processing Error: {str(e)}")
        
    return {"status": "success"}

@app.get("/api/analysis")
async def analyze_config():
    """
    分析配置，并标记来源
    """
    if not os.path.exists(OUTPUT_YAML) or os.path.getsize(OUTPUT_YAML) == 0:
        return {"status": "empty", "groups": [], "rules": [], "rule_count": 0, "regions": []}
    
    try:
        async with aiofiles.open(OUTPUT_YAML, 'r', encoding='utf-8') as f:
            content = await f.read()
            config = yaml.safe_load(content)
            if not config: return {"status": "empty"}
        
        user_config = {}
        try:
            if os.path.exists(CONFIG_JSON):
                async with aiofiles.open(CONFIG_JSON, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    user_config = json.loads(content)
        except: pass

        custom_group_names = set()
        for g in user_config.get('add_groups', []):
            if g.get('name'):
                custom_group_names.add(g['name'])
        
        custom_rules_map = {} 
        for r in user_config.get('add_rules', []):
            clean = clean_rule_for_clash(r)
            custom_rules_map[clean] = r

        rule_usage = Counter()
        final_display_rules = []
        
        for r in config.get('rules', []):
            target = get_rule_target(r)
            if target: rule_usage[target] += 1
            
            clean_r = clean_rule_for_clash(r)
            is_custom = clean_r in custom_rules_map
            display_str = custom_rules_map[clean_r] if is_custom else r
            
            final_display_rules.append({
                "str": display_str,
                "source": "custom" if is_custom else "native"
            })

        groups_info = []
        for g in config.get('proxy-groups', []):
            g_name = g['name']
            source = "custom" if g_name in custom_group_names else "native"
            groups_info.append({
                "name": g_name,
                "type": g.get('type', 'select'),
                "rule_count": rule_usage.get(g_name, 0),
                "source": source 
            })
        
        proxies = config.get('proxies', [])
        region_map = {
            "hk": "香港", "hong": "香港", "香港": "香港",
            "tw": "台湾", "tai": "台湾", "台湾": "台湾",
            "jp": "日本", "japan": "日本", "日本": "日本",
            "us": "美国", "america": "美国", "united": "美国", "美国": "美国",
            "sg": "新加坡", "sing": "新加坡", "新加坡": "新加坡",
            "kr": "韩国", "korea": "韩国", "韩国": "韩国",
            "uk": "英国", "gb": "英国", "英国": "英国",
            "de": "德国", "ger": "德国", "德国": "德国",
            "ca": "加拿大", "can": "加拿大", "加拿大": "加拿大",
            "tr": "土耳其", "tur": "土耳其", "土": "土耳其",
            "fr": "法国", "france": "法国", "法": "法国",
            "ru": "俄罗斯", "russia": "俄罗斯", "俄": "俄罗斯"
        }
        icons = {
            "香港": "🇭🇰", "台湾": "🇹🇼", "日本": "🇯🇵", "美国": "🇺🇸", 
            "新加坡": "🇸🇬", "韩国": "🇰🇷", "英国": "🇬🇧", "德国": "🇩🇪", 
            "加拿大": "🇨🇦", "土耳其": "🇹🇷", "法国": "🇫🇷", "俄罗斯": "🇷🇺", "其他": "🌐"
        }
        
        counts = {}
        for p in proxies:
            name = p.get('name', '').lower()
            found = False
            for k, v in region_map.items():
                if k in name:
                    if v not in counts: counts[v] = {"name": v, "icon": icons.get(v, "🌐"), "count": 0}
                    counts[v]['count'] += 1
                    found = True
                    break
            if not found:
                if "其他" not in counts: counts["其他"] = {"name": "其他", "icon": "🌐", "count": 0}
                counts["其他"]['count'] += 1
        
        regions = sorted(counts.values(), key=lambda x: x['count'], reverse=True)
        final_regions = [r for r in regions if r['name'] != '其他']
        if "其他" in counts: final_regions.append(counts["其他"])

        mtime = os.path.getmtime(OUTPUT_YAML)
        ts_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        
        return {
            "status": "success", 
            "groups": groups_info, 
            "rules": final_display_rules, 
            "rule_count": len(final_display_rules), 
            "regions": final_regions, 
            "total_nodes": len(proxies), 
            "update_time": ts_str,
            "ts": datetime.now().timestamp()
        }
    except Exception as e: return {"status": "error", "msg": str(e)}

# --- 静态文件挂载 ---
if os.path.exists("images"):
    app.mount("/images", StaticFiles(directory="images"), name="images")

app.mount("/", StaticFiles(directory="static", html=True), name="static")