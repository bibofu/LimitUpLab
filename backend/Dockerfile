# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS node-runtime

FROM python:3.13-slim-bookworm

ARG INSTALL_HITHINK_FINANCE=true

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    LIMITUPLAB_DATA_DIRECTORY=/app/data

WORKDIR /app

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
    && curl --fail --silent --show-error https://www.postgresql.org/media/keys/ACCC4CF8.asc \
         -o /usr/share/keyrings/postgresql.asc \
    && echo 'deb [signed-by=/usr/share/keyrings/postgresql.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' \
         > /etc/apt/sources.list.d/postgresql.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && if [ "$INSTALL_HITHINK_FINANCE" = "true" ]; then \
         ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
         && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
         && node --version \
         && npm --version \
         && npm install --global @hithink-tech/hithink-finance-cli; \
       fi \
    && rm -rf /var/lib/apt/lists/* /root/.npm

COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations

RUN useradd --create-home --uid 10001 limituplab \
    && mkdir -p /app/data \
    && chown -R limituplab:limituplab /app

USER limituplab

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
