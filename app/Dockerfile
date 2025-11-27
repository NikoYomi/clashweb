FROM python:3.10-slim

WORKDIR /app

# --- 优化 A-1 & B-1: 针对国内网络环境优化 ---
# 1. 替换 pip 源为清华源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 替换 Debian 系统源为清华源 (解决 apt-get update 卡住的问题)
# 注意：Python 3.10-slim 基于 Debian Bookworm 或 Trixie，使用 debian.sources 文件
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list

# 3. 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- 优化 C-3: 静态资源本地化 (国内 CDN 加速) ---
RUN mkdir -p static/lib

# 下载静态资源
# 使用 饿了么CDN (npm.elemecdn.com) 替代 unpkg.com，解决国内下载 Vue 卡住的问题
RUN apt-get update && apt-get install -y wget && \
    wget -O static/lib/vue.global.js https://npm.elemecdn.com/vue@3/dist/vue.global.js && \
    wget -O static/lib/tailwindcss.js https://cdn.tailwindcss.com && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]