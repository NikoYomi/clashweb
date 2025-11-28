FROM python:3.10-slim

ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8
RUN apt-get update && apt-get install -y locales && \
    locale-gen C.UTF-8 && \
    /usr/sbin/update-locale LANG=C.UTF-8

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y wget && \
    mkdir -p static/lib && \
    wget -O static/lib/vue.global.js https://unpkg.com/vue@3/dist/vue.global.js && \
    wget -O static/lib/tailwindcss.js https://cdn.tailwindcss.com && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
