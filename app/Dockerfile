FROM python:3.10-slim

WORKDIR /app

RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list

# 3. 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


RUN mkdir -p static/lib

RUN apt-get update && apt-get install -y wget && \
    wget -O static/lib/vue.global.js https://unpkg.com/vue@3/dist/vue.global.js && \
    wget -O static/lib/tailwindcss.js https://cdn.tailwindcss.com && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]

