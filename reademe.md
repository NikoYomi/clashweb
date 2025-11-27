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

- **📊 流量监控**：在面板首页实时查看机场订阅的流量使用情况（已用、剩余、过期时间）。
- **🔄 自动更新**：支持 Cron 表达式定时自动更新订阅，不仅下载配置，还能自动重启 Clash 容器使配置生效。
- **🛠️ 规则/策略组注入**：
  - 告别手动修改 `config.yaml`。
  - 在 Web 界面添加自定义策略组（如 "Emby", "OpenAI"）。
  - 添加自定义分流规则，更新订阅时**自动保留**，不会被覆盖。
- **🌐 订阅转换**：内置订阅转换逻辑，支持将各种机场链接转换为标准的 Clash 配置（默认使用公共后端，支持自定义）。
- **📝 实时日志**：内置 Web 日志终端，方便排查定时任务和转换状态。
- **🐳 纯净部署**：基于 Docker，不污染宿主机环境，前端资源本地化，无惧断网。

## 🖼️ 预览截图

> *(建议在此处放 1-2 张截图，例如仪表盘页面和规则管理页面)*
> ![Dashboard Screenshot](./screenshot_dashboard.png)

## 🚀 快速开始

### 前置要求
- Docker & Docker Compose
- 已经运行的 Clash 容器（本服务作为 Sidecar 伴侣容器运行）

### 1. 创建 `docker-compose.yml`

将 `ClashWeb` 与你的 `Clash` 容器放在同一个网络下，并挂载相同的配置目录。

```yaml
version: '3.8'

services:
  # 你的 Clash 核心服务 (示例)
  clash:
    image: dreamacro/clash-premium
    container_name: clash
    restart: always
    volumes:
      - ./clash_data:/root/.config/clash # 注意这里
    ports:
      - "7890:7890"
      - "9090:9090"

  # ClashWeb 管理面板
  clashweb:
    image: your-dockerhub-username/clashweb:latest # 或者 build: .
    container_name: clash_web
    restart: always
    ports:
      - "9086:80"
    volumes:
      - ./clash_data:/data                 # 必须映射到容器内的 /data
      - /var/run/docker.sock:/var/run/docker.sock # 用于重启 Clash 容器
    environment:
      - TZ=Asia/Shanghai