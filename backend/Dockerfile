# syntax=docker/dockerfile:1

FROM python:3.13-slim

ARG INSTALL_HITHINK_FINANCE=true

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    LIMITUPLAB_DATABASE_PATH=/app/data/limituplab.sqlite

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
    && if [ "$INSTALL_HITHINK_FINANCE" = "true" ]; then \
         apt-get install -y --no-install-recommends nodejs npm \
         && npm install --global @hithink-tech/hithink-finance-cli; \
       fi \
    && rm -rf /var/lib/apt/lists/* /root/.npm

COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY backend/app ./app
COPY backend/scripts ./scripts

RUN useradd --create-home --uid 10001 limituplab \
    && mkdir -p /app/data \
    && chown -R limituplab:limituplab /app

USER limituplab

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
