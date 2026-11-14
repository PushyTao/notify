FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATA_DIR=/data ASR_CACHE_DIR=/data/models HF_HOME=/data/models/huggingface
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home app \
    && mkdir /data && chown app:app /data
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY --chown=app:app app ./app
ARG APP_VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="长虹" \
    org.opencontainers.image.description="抖音视频更新、语音转写、AI 总结与邮件订阅" \
    org.opencontainers.image.source="https://github.com/PushyTao/notify" \
    org.opencontainers.image.version=$APP_VERSION \
    org.opencontainers.image.revision=$VCS_REF
USER app
EXPOSE 8080
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
