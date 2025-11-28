# 使用你要求的 python 3.10-slim
FROM python:3.10-slim

# --- 核心修复：设置 UTF-8 编码 ---
# 解决 "Uncaught SyntaxError" 和页面乱码的关键
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# 环境变量：防止生成 pyc 文件，并在日志中实时输出
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 设置时区（与你的 compose 保持一致）
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# 先复制依赖文件并安装（利用 Docker 缓存加速构建）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制其余项目文件
COPY . .

# --- 端口适配 ---
# 你的 compose 写的是 9086:80，所以容器内部必须暴露 80
EXPOSE 80

# --- 启动命令 ---
# 注意：这里必须指定 --port 80，否则 uvicorn 默认会跑在 8000 上导致连接拒绝
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]