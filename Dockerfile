FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS backend-dev

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY README.md ./
COPY backend/pyproject.toml backend/uv.lock ./backend/

WORKDIR /app/backend
# 第三方依赖只由项目元数据决定，业务源码变化时可复用这一层。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --dev --no-install-project

COPY backend /app/backend

# 源码复制完成后只需安装本地项目，下载缓存可在重试构建时继续复用。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --dev

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "customer_service.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]


FROM node:22-alpine AS frontend-dev

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
