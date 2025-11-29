FROM python:3.10-slim

WORKDIR /app

# --- 优化: 国际网络环境构建 ---
# 1. 移除国内源设置，直接使用官方 PyPI 和 Debian 源 (在 GitHub Actions 环境下速度极快)
# 2. 安装 wget 用于下载静态资源
RUN apt-get update && \
    apt-get install -y --no-install-recommends wget && \
    rm -rf /var/lib/apt/lists/*

# 3. 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- 静态资源本地化 ---
# 使用国际通用的 unpkg 和 tailwindcss 官方 CDN
RUN mkdir -p static/lib && \
    wget -O static/lib/vue.global.js https://unpkg.com/vue@3/dist/vue.global.js && \
    wget -O static/lib/tailwindcss.js https://cdn.tailwindcss.com

COPY . .

EXPOSE 80

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]