# syntax=docker/dockerfile:1
FROM python:3.11.15-slim-bookworm AS backend
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

ARG INSTALL_AI=0
COPY requirements.txt requirements-ai.txt requirements-deploy.lock ./
RUN pip install --no-cache-dir --only-binary=:all: --no-deps -r requirements-deploy.lock \
    && pip check \
    && if [ "$INSTALL_AI" = 1 ]; then \
        apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
        && rm -rf /var/lib/apt/lists/* \
        && pip install --no-cache-dir -c requirements-deploy.lock -r requirements-ai.txt; \
    fi \
    && groupadd -g 10001 ecokitchen \
    && useradd -u 10001 -g 10001 -M -s /usr/sbin/nologin ecokitchen \
    && install -d -m 0700 -o 10001 -g 10001 /app/instance

COPY *.py ./
COPY forecasting/ ./forecasting/
COPY templates/ ./templates/
COPY static/ ./static/
COPY data/ ./data/
COPY deploy/start_backend.py deploy/gunicorn.conf.py ./deploy/

USER 10001:10001
ENV PYTHONPATH=/app HOME=/tmp
EXPOSE 8000
CMD ["python", "-m", "deploy.start_backend"]

FROM node:24.22.2-bookworm-slim AS dashboard-build
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/index.html dashboard/vite.config.js ./
COPY dashboard/src/ ./src/
RUN VITE_API_BASE_URL='' npm run build

FROM nginx:1.30.8-alpine AS web
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=dashboard-build /dashboard/dist/ /usr/share/nginx/html/
