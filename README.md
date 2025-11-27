# 🐱 ClashWeb - 你的 Clash 订阅伴侣

<div align="center">
  <img src="./app/images/icon.png" alt="ClashWeb Logo" width="120">
  <br>
  <br>
  
  [![Docker](https://img.shields.io/badge/Docker-Enabled-blue?logo=docker)](https://www.docker.com/)
  [![Python](https://img.shields.io/badge/Python-3.10-yellow?logo=python)](https://www.python.org/)
  [![Vue 3](https://img.shields.io/badge/Frontend-Vue.js-green?logo=vue.js)](https://vuejs.org/)
  [![License](https://img.shields.io/badge/License-MIT-orange.svg)](LICENSE)

  <p>一个轻量级的 Clash 配置文件管理面板，专为 Docker 部署环境设计。<br>自动更新订阅、注入自定义规则、监控流量使用，并自动重启 Clash 容器。</p>
</div>

## ✨ 主要特性

- **📊 流量监控**：查看机场订阅的流量使用情况（已用、剩余、过期时间）。
- **🔄 自动更新**：支持 Cron 表达式定时自动更新订阅。
- **🛠️ 规则/策略组注入**：
  - 告别手动修改 `config.yaml`。
  - 在 Web 界面添加自定义策略组。
  - 添加自定义分流规则。
- **🌐 订阅转换**：内置订阅转换逻辑，支持将各种机场链接转换为标准的 Clash 配置。
- **📝 实时日志**：内置 Web 日志终端，方便排查定时任务和转换状态。
- **🐳 纯净部署**：基于 Docker。

## 🖼️ 预览截图

<img width="1636" height="929" alt="image" src="https://github.com/user-attachments/assets/90240aaf-0715-488a-89cf-c9c8b1a2ea09" />
<img width="1535" height="925" alt="image" src="https://github.com/user-attachments/assets/4d478f9c-542e-4b5c-966f-4046a782dfe3" />



## 🚀 快速开始

### 前置要求
- Docker & Docker Compose
- 已经运行的 Clash 容器（本服务作为 Sidecar 伴侣容器运行）

### 1. 创建 `docker-compose.yml`

该项目建议与 yard 、 clash 搭配使用，并挂载相同的配置目录。

```yaml
version: '3.8'

services:
  # ClashWeb 管理面板
  clashweb:
    image: nikoyomi/clashweb:latest
    container_name: clashweb
    restart: always
    ports:
      - "9086:80"
    volumes:
      - ./data:/data                 # 必须映射到clash配置文件夹
      - /var/run/docker.sock:/var/run/docker.sock # 用于重启 Clash 容器
    environment:
      - TZ=Asia/Shanghai
````

### 2\. 启动服务

```bash
docker-compose up -d
```

### 3\. 访问面板

浏览器访问：`http://localhost:9086`

## 📖 使用指南

1.  **初始化设置**：

      * 进入面板 -\> 点击右上角 **设置**。
      * **转换后端**：需要填入 Subconverter 后端地址才能进行转换。（建议自己搭建后端地址）
      * **重启容器**：填入你的 Clash 容器名称（例如上面配置中的 `clash`）。
      * **定时任务**：开启自动更新，设置 Cron 表达式（如 `0 4 * * *` 每天凌晨4点）。

2.  **添加订阅**：

      * 在仪表盘粘贴机场订阅链接，点击“立即更新订阅”。
      * 系统会自动下载、转换、获取流量信息，并生成 `config.yaml` 到/data挂载目录。

3.  **自定义规则**：

      * 在“代理组”页签添加新的策略组。
      * 在“规则管理”页签添加自定义规则，并指定目标策略组。
      * 下次更新订阅时，这些规则会自动插入到配置文件最前方。

## 🛠️ 开发与构建

如果你想自己修改代码并构建镜像：

```bash
# 克隆仓库
git clone [https://github.com/nikoyomi/clashweb.git](https://github.com/nikoyomi/clashweb.git)
cd clashweb

# 构建镜像 (已配置国内镜像源加速)
docker-compose build

# 运行
docker-compose up -d
```

## ⚠️ 免责声明

本项目仅用于技术交流与个人配置管理。开发者不对使用本项目产生的任何后果负责。请遵守当地法律法规。

## 📄 License

[MIT License](https://www.google.com/search?q=LICENSE)

```
