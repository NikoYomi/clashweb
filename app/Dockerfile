FROM python:3.10-slim

WORKDIR /app

# --- 优化 A-1 & B-1: 针对国内网络环境优化 ---
# 1. 替换 pip 源为清华源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 替换 Debian 系统源为清华源
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list

# 3. 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 创建静态资源目录并下载资源（增加重试和验证）
RUN mkdir -p static/lib

# 下载静态资源，增加重试机制和备用CDN
RUN apt-get update && apt-get install -y wget && \
    # 下载 Vue.js，如果第一个CDN失败，尝试备用CDN
    (wget --tries=3 --timeout=30 -O static/lib/vue.global.js https://npm.elemecdn.com/vue@3/dist/vue.global.js || \
     wget --tries=3 --timeout=30 -O static/lib/vue.global.js https://unpkg.com/vue@3/dist/vue.global.js) && \
    # 下载 TailwindCSS
    (wget --tries=3 --timeout=30 -O static/lib/tailwindcss.js https://cdn.tailwindcss.com || \
     wget --tries=3 --timeout=30 -O static/lib/tailwindcss.js https://unpkg.com/tailwindcss@3/dist/tailwindcss.min.js) && \
    # 验证文件是否下载成功
    (test -s static/lib/vue.global.js && echo "Vue.js downloaded successfully" || (echo "Vue.js download failed" && exit 1)) && \
    (test -s static/lib/tailwindcss.js && echo "TailwindCSS downloaded successfully" || (echo "TailwindCSS download failed" && exit 1)) && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 5. 复制应用代码
COPY . .

# 6. 确保文件权限正确
RUN chmod -R 755 static/ && \
    chown -R www-data:www-data static/

# 7. 健康检查（可选）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:80/')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
