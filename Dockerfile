# Dockerfile پرتابل — دو ربات تلگرام، بدون Gradio و بدون وابستگی به Hugging Face
# مناسب: Northflank، Render، Back4App Containers، Koyeb، Fly.io، Oracle/هر VPS، Docker معمولی
#
# ساخت:   docker build -t mybots .
# اجرا:   docker run --rm -p 7860:7860 \
#           -e BOT_TOKEN=... -e ADMIN_IDS=... -e TG_API_ID=... -e TG_API_HASH=... \
#           -e DOR_BOT_TOKEN=... -e DOR_ADMIN_ID=... -e LICENSE_PRIVATE_KEY=... \
#           -e HF_TOKEN=... -e DATA_REPO=yourname/bot-data \
#           -e PUBLIC_URL=https://your-app.example.com \
#           mybots

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=7860 \
    DATA_DIR=/data \
    TZ=Asia/Tehran

# ffmpeg برای فشرده‌سازی/صوت + ca-certificates/tzdata برای HTTPS و ساعت ایران
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata curl xz-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# پوشه داده (اگر حجم/Volume نبندید، با هر ری‌استارت پاک می‌شود — همان کار HF)
RUN mkdir -p /data

EXPOSE 7860

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["python", "-u", "run_all.py"]
