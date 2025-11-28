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
LOG_FILE = os.path.join(DATA_PATH, "app.log")
DEFAULT_BACKEND = "https://api.v1.mk/sub?target=clash&url="

# --- 初始化日志 ---
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
    name: str = "" 
    webUrl: str = "" # [新增] 机场官网地址
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
        with open(CONFIG_JSON, 'w') as f: json.dump(ConfigModel().dict(), f)

    if not os.path.exists(OUTPUT_YAML) or os.path.isdir(OUTPUT_YAML):
        with open(OUTPUT_YAML, 'w') as f: f.write("")

def refresh_scheduler():
    try:
        with open(CONFIG_JSON, 'r') as f:
            data = json.load(f)
        
        job_id = 'auto_update_job'
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        if data.get('auto_update') and data.get('cron_expression'):
            cron_str = data['cron_expression']
            try:
                trigger = CronTrigger.from_crontab(cron_str, timezone=tz)
                scheduler.add_job(scheduled_update_task, trigger, id=job_id, replace_existing=True)
                logger.info(f"✅ 定时任务已设置: [{cron_str}]")
            except Exception as e:
                logger.error(f"Invalid cron expression: {e}")
        else:
            logger.info("⛔️ 定时任务已关闭")
    except Exception as e:
        logger.error(f"Scheduler refresh failed: {e}")
async def scheduled_update_task():
    logger.info(">>> ⏳ 开始执行定时更新任务 <<<")
    try:
        async with aiofiles.open(CONFIG_JSON, 'r') as f:
            content = await f.read()
            data = json.loads(content)
        
        url = data.get('sub_url')
        if not url:
            logger.warning("未配置订阅链接，跳过更新")
            return

        # 执行更新逻辑
        await internal_process_subscription(url, data)
        
        # 保存 user_info 更新
        async with aiofiles.open(CONFIG_JSON, 'w') as f:
            await f.write(json.dumps(data, indent=2))

        logger.info("✅ 定时更新任务完成")

        # [修复] 兼容中文逗号
        container_str = data.get('restart_containers', '').replace('，', ',')
        if container_str:
            try:
                client = docker.from_env()
                targets = [name.strip() for name in container_str.split(',') if name.strip()]
                for name in targets:
                    try:
                        client.containers.get(name).restart()
                        logger.info(f"✅ 容器已重启: {name}")
                    except Exception as e:
                        logger.error(f"❌ 重启容器 {name} 失败: {e}")
            except Exception as e:
                logger.error(f"Docker 连接失败: {e}")
                    
    except Exception as e:
        logger.error(f"❌ 定时任务执行出错: {e}")

# --- 逻辑分离：任务1 获取原始流量信息和机场名称 ---
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
                
                # B. Content-Disposition 提取
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

                # 3. [新增] 提取官网地址 (webUrl)
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
                        # 简单的假设：订阅域名的根通常是官网
                        web_url = f"{parsed.scheme}://{parsed.netloc}"
                    except: pass

                result = {
                    "name": airport_name,
                    "webUrl": web_url, # [新增]
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

# --- 逻辑分离：任务2 下载并转换配置 ---
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
    并发执行：
    1. 从原始链接获取流量信息和名称
    2. 从转换后端获取配置文件
    
    返回：fetch_original_userinfo 的结果 (可能为 None)
    """
    
    task_traffic = fetch_original_userinfo(url)
    task_download = download_and_convert_config(url, data)
    
    results = await asyncio.gather(task_traffic, task_download, return_exceptions=True)
    
    fetched_user_info = results[0]
    download_result = results[1]
    
    # 处理流量信息结果
    if isinstance(fetched_user_info, dict):
        data['user_info'] = fetched_user_info
        # 注意：这里只更新内存中的 data 对象，调用者负责写入 config.json
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
    return rule_str.split('#')[0].strip()

def apply_patch(config: dict, patch: dict) -> dict:
    config['allow-lan'] = True
    config['external-controller'] = '0.0.0.0:9090'
    if 'bind-address' in config: config['bind-address'] = '*'

    reference_proxies = ["DIRECT", "REJECT"]
    source_groups = config.get('proxy-groups', [])
    for g in source_groups:
        if g.get('type') == 'select' and len(g.get('proxies', [])) > 3:
            reference_proxies = g['proxies']
            break

    del_groups_list = patch.get('del_groups') or []
    add_rules_raw = patch.get('add_rules') or []

    if del_groups_list:
        config['proxy-groups'] = [g for g in config.get('proxy-groups', []) if g['name'] not in del_groups_list]
        new_base_rules = []
        for rule in config.get('rules', []):
            if get_rule_target(rule) not in del_groups_list:
                new_base_rules.append(rule)
        config['rules'] = new_base_rules
        
        valid_add_rules = []
        for rule in add_rules_raw:
            if get_rule_target(rule) not in del_groups_list:
                valid_add_rules.append(rule)
        add_rules_raw = valid_add_rules

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

    del_keywords = patch.get('del_rules') or []
    if del_keywords:
        final_rules = []
        for rule in config.get('rules', []):
            clean_rule = clean_rule_for_clash(rule)
            if not any(k in clean_rule for k in del_keywords): 
                final_rules.append(rule)
        config['rules'] = final_rules

    if add_rules_raw:
        for r in reversed(add_rules_raw): 
            clean_r = clean_rule_for_clash(r)
            if clean_r:
                config.setdefault('rules', []).insert(0, clean_r)
             
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
            async with aiofiles.open(CONFIG_JSON, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                # 确保 user_info 结构完整
                if 'user_info' not in data:
                    data['user_info'] = UserInfo().dict()
                else:
                    # 补全可能缺失的字段 (如 webUrl)
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
        async with aiofiles.open(CONFIG_JSON, 'w') as f:
            await f.write(json.dumps(payload, indent=2))
        
        refresh_scheduler()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"保存配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/backup")
async def backup_config(include_sub: bool = False):
    if not os.path.exists(CONFIG_JSON): raise HTTPException(status_code=404, detail="No config found")
    try:
        async with aiofiles.open(CONFIG_JSON, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
        if not include_sub:
            data['sub_url'] = ""
            data['sub_history'] = []
            
        temp_path = "/tmp/clashweb_backup.json"
        async with aiofiles.open(temp_path, 'w') as f:
            await f.write(json.dumps(data, indent=2))
            
        return FileResponse(temp_path, filename="clashweb_backup.json", media_type="application/json")
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.post("/api/restore")
async def restore_config(file: UploadFile = File(...), restore_sub: bool = Form(False)):
    try:
        content = await file.read()
        backup_data = json.loads(content)
        if not isinstance(backup_data, dict): raise ValueError("Format Error")
        
        final_data = backup_data
        if not restore_sub:
            current_data = {}
            if os.path.exists(CONFIG_JSON):
                with open(CONFIG_JSON, 'r') as f: current_data = json.load(f)
            final_data['sub_url'] = current_data.get('sub_url', '')
            final_data['sub_history'] = current_data.get('sub_history', [])
        
        if restore_sub and not final_data.get('sub_url'):
             raise ValueError("备份文件中未包含订阅信息")

        async with aiofiles.open(CONFIG_JSON, "w") as f:
            await f.write(json.dumps(final_data, indent=2))
        
        refresh_scheduler()
        
        summary = {
            "groups": len(final_data.get('add_groups', [])),
            "rules": len(final_data.get('add_rules', [])),
            "sub_status": "已覆盖" if restore_sub else "未变更",
            "has_sub": bool(final_data.get('sub_url'))
        }
        return {"status": "success", "summary": summary}
    except Exception as e:
        logger.error(f"还原失败: {e}")
        raise HTTPException(status_code=400, detail=f"Restore Failed: {str(e)}")

@app.post("/api/restart_containers")
async def restart_containers():
    try:
        async with aiofiles.open(CONFIG_JSON, 'r') as f:
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
        async with aiofiles.open(CONFIG_JSON, 'r') as f:
            content = await f.read()
            data = json.loads(content)
    except: data = {}
    
    # 临时更新 URL 以供 process 逻辑使用
    data['sub_url'] = req.url
    
    # 尝试处理订阅 (下载 + 获取信息)
    try:
        # 获取最新信息 (fetched_info 是 fetch_original_userinfo 的返回值)
        fetched_info = await internal_process_subscription(req.url, data)
        
        # --- 核心更新逻辑：更新历史记录 ---
        airport_name = "未知机场"
        traffic_info = {}
        
        if fetched_info:
            airport_name = fetched_info.get("name", "未知机场")
            traffic_info = {
                "upload": fetched_info.get("upload", 0),
                "download": fetched_info.get("download", 0),
                "total": fetched_info.get("total", 0),
                "expire": fetched_info.get("expire", 0)
            }
        
        # 2. 更新 history
        history = data.get('sub_history', [])
        # 移除已存在的该 URL 记录 (避免重复)
        history = [h for h in history if h.get('url') != req.url]
        
        # 构建新记录 (包含名称和流量快照)
        new_record = {
            "url": req.url,
            "date": datetime.now().strftime('%Y-%m-%d %H:%M'),
            "name": airport_name,
            "info": traffic_info
        }
        history.insert(0, new_record)
        
        if len(history) > 10: history = history[:10]
        data['sub_history'] = history
        
        # 保存到文件 (包含 user_info 的更新)
        async with aiofiles.open(CONFIG_JSON, 'w') as f:
            await f.write(json.dumps(data, indent=2))
            
    except Exception as e:
        logger.error(f"处理订阅出错: {e}")
        # 出错时也要尝试保存下 URL，防止用户丢失输入
        async with aiofiles.open(CONFIG_JSON, 'w') as f:
            await f.write(json.dumps(data, indent=2))
        raise HTTPException(status_code=500, detail=f"Processing Error: {str(e)}")
        
    return {"status": "success"}

@app.get("/api/analysis")
async def analyze_config():
    if not os.path.exists(OUTPUT_YAML) or os.path.getsize(OUTPUT_YAML) == 0:
        return {"status": "empty", "groups": [], "rules": [], "rule_count": 0, "regions": []}
    
    try:
        async with aiofiles.open(OUTPUT_YAML, 'r', encoding='utf-8') as f:
            content = await f.read()
            config = yaml.safe_load(content)
            if not config: return {"status": "empty"}
        
        json_rules_map = {}
        try:
            if os.path.exists(CONFIG_JSON):
                async with aiofiles.open(CONFIG_JSON, 'r') as f:
                    content = await f.read()
                    saved_data = json.loads(content)
                    for r in saved_data.get('add_rules', []):
                        clean = clean_rule_for_clash(r)
                        json_rules_map[clean] = r
        except: pass

        rule_usage = Counter()
        final_display_rules = []
        
        for r in config.get('rules', []):
            target = get_rule_target(r)
            if target: rule_usage[target] += 1
            if r in json_rules_map:
                final_display_rules.append(json_rules_map[r])
            else:
                final_display_rules.append(r)

        groups_info = [{"name": g['name'], "type": g.get('type', 'select'), "rule_count": rule_usage.get(g['name'], 0)} for g in config.get('proxy-groups', [])]
        
        proxies = config.get('proxies'， [])
        region_map = {
            "hk": "香港"， "hong": "香港", "香港": "香港",
            "tw": "台湾"， "tai": "台湾", "台湾": "台湾",
            "jp": "日本", "japan": "日本", "日本": "日本",
            "us": "美国", "america": "美国", "united": "美国", "美国": "美国",
            "sg": "新加坡", "sing": "新加坡", "新加坡": "新加坡",
            "kr": "韩国", "korea": "韩国", "韩国": "韩国",
            "uk": "英国", "gb": "英国", "英国": "英国",
            "de": "德国", "ger": "德国", "德国": "德国",
            "ca": "加拿大", "can": "加拿大", "加拿大": "加拿大",
            "tr": "土耳其", "tur": "土耳其", "土": "土耳其",
            "fr": "法国", "france": "法国", "法": "法国",
            "ru": "俄罗斯"， "russia": "俄罗斯", "俄": "俄罗斯"
        }
        icons = {
            "香港": "🇭🇰"， "台湾": "🇹🇼", "日本": "🇯🇵", "美国": "🇺🇸", 
            "新加坡": "🇸🇬"， "韩国": "🇰🇷", "英国": "🇬🇧", "德国": "🇩🇪", 
            "加拿大": "🇨🇦", "土耳其": "🇹🇷", "法国": "🇫🇷", "俄罗斯": "🇷🇺", "其他": "🌐"
        }
        
        counts = {}
        for p in proxies:
            name = p.get('name', '').lower()
            found = False
            for k, v 在 region_map.items():
                if k 在 name:
                    if v not 在 counts: counts[v] = {"name": v, "icon": icons.get(v, "🌐"), "count": 0}
                    counts[v]['count'] += 1
                    found = True
                    break
            if not found:
                if "其他" not 在 counts: counts["其他"] = {"name": "其他", "icon": "🌐", "count": 0}
                counts["其他"]['count'] += 1
        
        regions = sorted(counts.values(), key=lambda x: x['count'], reverse=True)
        final_regions = [r for r 在 regions if r['name'] != '其他']
        if "其他" 在 counts: final_regions.append(counts["其他"])

        mtime = os.path。getmtime(OUTPUT_YAML)
        ts_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        
        return {
            "status": "success", 
            "groups": groups_info, 
            "rules": final_display_rules, 
            "rule_count": len(final_display_rules), 
            "regions": final_regions, 
            "total_nodes": len(proxies), 
            "update_time": ts_str,
            "ts": datetime.当前()。timestamp()
        }
    except Exception as e: return {"status": "error", "msg": str(e)}

# --- 静态文件挂载 ---
if os.path.exists("images"):
    app.mount("/images"， StaticFiles(directory="images"), name="images")

app.mount("/", StaticFiles(directory="static", html=True), name="static")
