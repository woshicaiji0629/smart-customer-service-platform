.PHONY: \
	help \
	backend-sync \
	backend-ruff \
	backend-pyright \
	backend-test \
	backend-check \
	backend-init-db \
	backend-serve \
	frontend-sync \
	frontend-dev \
	frontend-lint \
	frontend-test \
	frontend-build \
	frontend-check \
	check \
	postgres-up \
	postgres-down \
	postgres-logs \
	redis-up \
	redis-down \
	redis-logs \
	infra-up \
	infra-down \
	infra-logs \
	database-init-run \
	backend-container-up \
	backend-container-down \
	backend-container-logs \
	frontend-container-up \
	frontend-container-down \
	frontend-container-logs \
	knowledge-index \
	knowledge-search \
	evaluate-conversation-flows \
	app-up \
	compose-ps \
	compose-logs \
	compose-restart \
	compose-build \
	compose-down \
	docker-build \
	require-dashscope-api-key \
	require-query

# 默认执行 make 时显示帮助，避免误启动服务。
.DEFAULT_GOAL := help

# 本地后端脚本默认连接 docker compose 暴露到宿主机的 PostgreSQL 和 Redis。
BACKEND_DATABASE_URL ?= postgresql+asyncpg://customer_service:customer_service_dev@localhost:5432/smart_customer_service
BACKEND_REDIS_URL ?= redis://localhost:6379/0
BACKEND_ENV = DATABASE_URL="$(BACKEND_DATABASE_URL)" REDIS_URL="$(BACKEND_REDIS_URL)" MOCK_AUTH_ENABLED=true SESSION_COOKIE_SECURE=false

# 可覆盖参数：
#   make knowledge-index KNOWLEDGE_INDEX_LIMIT=10
#   make knowledge-search QUERY='提现状态' SEARCH_LIMIT=5
KNOWLEDGE_INDEX_LIMIT ?= 3
SEARCH_LIMIT ?= 5

help: ## 显示可用命令和参数说明。
	@printf "用法：make <目标> [参数=值]\n\n"
	@printf "常用目标：\n"
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-30s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\n常用参数：\n"
	@printf "  %-30s %s\n" "KNOWLEDGE_INDEX_LIMIT=3" "知识库索引构建数量。"
	@printf "  %-30s %s\n" "QUERY='提现状态'" "知识库语义检索问题，knowledge-search 必填。"
	@printf "  %-30s %s\n" "SEARCH_LIMIT=5" "知识库语义检索返回数量。"
	@printf "  %-30s %s\n" "BACKEND_DATABASE_URL=..." "覆盖本地后端脚本连接的数据库地址。"
	@printf "  %-30s %s\n" "BACKEND_REDIS_URL=..." "覆盖本地后端脚本连接的 Redis 地址。"
	@printf "\n必要环境变量：\n"
	@printf "  %-30s %s\n" "DASHSCOPE_API_KEY" "knowledge-index 和 knowledge-search 必填。"

backend-sync: ## 安装或同步后端开发依赖。
	cd backend && uv sync --dev

backend-ruff: ## 运行 Ruff 静态检查。
	cd backend && uv run ruff check .

backend-pyright: ## 运行 Pyright 严格类型检查。
	cd backend && uv run pyright

backend-test: ## 运行后端 pytest 测试。
	cd backend && uv run pytest

backend-check: backend-ruff backend-pyright backend-test ## 运行后端完整质量门禁：Ruff、Pyright、pytest。

frontend-sync: ## 使用 package-lock.json 同步前端依赖。
	cd frontend && npm ci

frontend-dev: ## 启动前端 Vite 开发服务。
	cd frontend && npm run dev

frontend-lint: ## 运行前端 ESLint 检查。
	cd frontend && npm run lint

frontend-test: ## 运行前端 Vitest 测试。
	cd frontend && npm run test

frontend-build: ## 运行前端 TypeScript 编译和 Vite 构建。
	cd frontend && npm run build

frontend-check: frontend-lint frontend-test frontend-build ## 运行前端完整质量门禁：ESLint、测试、类型编译和构建。

check: backend-check frontend-check ## 运行前后端完整质量门禁。

postgres-up: ## 只启动 PostgreSQL。
	docker compose up -d postgres

postgres-down: ## 只停止 PostgreSQL；不删除数据卷。
	docker compose stop postgres

postgres-logs: ## 查看 PostgreSQL 日志。
	docker compose logs -f postgres

redis-up: ## 只启动 Redis。
	docker compose up -d redis

redis-down: ## 只停止 Redis；不删除数据卷。
	docker compose stop redis

redis-logs: ## 查看 Redis 日志。
	docker compose logs -f redis

infra-up: ## 只启动 PostgreSQL 和 Redis；不会启动后端和前端。
	docker compose up -d postgres redis

infra-down: ## 停止 PostgreSQL 和 Redis；不删除数据卷。
	docker compose stop postgres redis

infra-logs: ## 同时查看 PostgreSQL 和 Redis 日志。
	docker compose logs -f postgres redis

database-init-run: ## 使用 Compose 运行一次数据库初始化任务。
	docker compose up database-init

backend-container-up: backend-check ## 质量门禁通过后在后台启动后端容器及其依赖。
	docker compose up -d --build backend

backend-container-down: ## 只停止后端容器。
	docker compose stop backend

backend-container-logs: ## 查看后端容器日志。
	docker compose logs -f backend

frontend-container-up: frontend-check ## 前端门禁通过后在后台启动前端容器及其依赖。
	docker compose up -d --build frontend

frontend-container-down: ## 只停止前端容器。
	docker compose stop frontend

frontend-container-logs: ## 查看前端容器日志。
	docker compose logs -f frontend

backend-init-db: infra-up ## 确保基础设施启动后初始化数据库结构。
	cd backend && $(BACKEND_ENV) uv run python -m script.init_database

backend-serve: backend-init-db ## 确保基础设施和数据库就绪后启动本地后端。
	cd backend && $(BACKEND_ENV) uv run uvicorn customer_service.main:app --reload

knowledge-index: ## 构建知识库索引；需要 DASHSCOPE_API_KEY。
	@$(MAKE) require-dashscope-api-key
	@$(MAKE) backend-init-db
	cd backend && $(BACKEND_ENV) uv run python -m script.build_knowledge_index --limit $(KNOWLEDGE_INDEX_LIMIT)

knowledge-search: ## 检索知识库；必须传 QUERY='问题'。
	@$(MAKE) require-query
	@$(MAKE) require-dashscope-api-key
	@$(MAKE) infra-up
	cd backend && $(BACKEND_ENV) uv run python -m script.search_knowledge "$(QUERY)" --limit $(SEARCH_LIMIT)

evaluate-conversation-flows: ## 运行对话主流程评估；不需要启动 Docker 基础设施。
	cd backend && $(BACKEND_ENV) uv run python -m script.evaluate_conversation_flows

app-up: check ## 质量门禁通过后在后台启动完整 Docker Compose 应用。
	docker compose up -d --build

compose-ps: ## 查看 Docker Compose 服务状态。
	docker compose ps

compose-logs: ## 查看全部 Docker Compose 服务日志。
	docker compose logs -f

compose-restart: ## 重启当前 Docker Compose 项目内已创建的服务。
	docker compose restart

compose-build: check ## 质量门禁通过后构建 Compose 镜像。
	docker compose build

compose-down: ## 停止并移除 Docker Compose 服务容器。
	docker compose down

docker-build: backend-check ## 质量门禁通过后构建后端开发镜像。
	docker build --target backend-dev .

require-dashscope-api-key:
	@if [ -z "$$DASHSCOPE_API_KEY" ]; then \
		printf "%s\n" "缺少环境变量：DASHSCOPE_API_KEY"; \
		printf "%s\n" "用法：DASHSCOPE_API_KEY='你的 Key' make knowledge-index"; \
		printf "%s\n" "或：  DASHSCOPE_API_KEY='你的 Key' make knowledge-search QUERY='提现状态'"; \
		exit 2; \
	fi

require-query:
	@if [ -z "$(strip $(QUERY))" ]; then \
		printf "%s\n" "缺少参数：QUERY"; \
		printf "%s\n" "用法：make knowledge-search QUERY='提现状态'"; \
		exit 2; \
	fi
