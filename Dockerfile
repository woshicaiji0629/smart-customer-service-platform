FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS backend-dev

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY README.md ./
COPY backend ./backend
COPY backend/docker-entrypoint.sh /usr/local/bin/backend-entrypoint
RUN chmod +x /usr/local/bin/backend-entrypoint

WORKDIR /app/backend
RUN uv sync --locked --dev

EXPOSE 8000
ENTRYPOINT ["backend-entrypoint"]
CMD ["uv", "run", "uvicorn", "customer_service.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]


FROM node:22-alpine AS frontend-dev

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
