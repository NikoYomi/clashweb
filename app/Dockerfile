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
    mkdir -p static/lib

RUN download_file() { \
    local url="$1"; \
    local path="$2"; \
    for i in 1 2 3 4 5; do \
        echo "Attempt $i: Downloading $url to $path"; \
        if wget -O "$path" "$url"; then \
            echo "Successfully downloaded $path"; \
            return 0; \
        else \
            echo "Download failed. Retrying in 5 seconds..."; \
            sleep 5; \
        fi; \
    done; \
    echo "Failed to download $path after multiple retries."; \
    return 1; \
} && \
    export -f download_file && \
    \

    download_file "https://unpkg.com/vue@3/dist/vue.global.prod.js" "static/lib/vue.global.js" && \
    \

    download_file "https://cdn.tailwindcss.com" "static/lib/tailwindcss.js" && \
    \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
