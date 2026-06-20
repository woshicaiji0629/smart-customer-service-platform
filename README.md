# Smart Customer Service Platform

交易所智能客服平台，前后端使用同一个仓库管理。

## 目录

- `backend/`：Python 后端，使用 FastAPI 和 uv。
- `frontend/`：React、TypeScript 和 Vite 前端。

## Docker Compose 本地开发

Docker Compose 同时启动前端、后端、Redis 和带 pgvector 的 PostgreSQL。
宿主机不需要单独安装数据库或 Redis。Mock 登录、会话和业务查询无需模型服务
Key，可以直接启动：

```bash
docker compose up --build
```

需要使用知识库检索和 RAG 问答时，再设置百炼 API Key：

```bash
export DASHSCOPE_API_KEY='百炼 API Key'
docker compose up --build
```

也可以把 Key 保存在仓库根目录中不会提交到 Git 的 `.env` 文件里。未配置 Key
时，知识库问答接口返回 503，不影响 Mock 业务查询。PostgreSQL 默认使用仅限
本地开发的用户 `customer_service` 和密码 `customer_service_dev`，可通过
`POSTGRES_PASSWORD` 覆盖。

首次启动会自动创建 pgvector 扩展和应用表。PostgreSQL 与 Redis 数据分别保存
在 `.local-data/postgres/` 和 `.local-data/redis/`，停止或重建容器不会丢失。
这些目录只包含本地运行数据，不会提交到 Git，也不要手动修改其中的文件。

前端地址为 `http://localhost:5173`，后端 API 文档地址为
`http://localhost:8000/docs`。本地前后端统一使用 `localhost`，确保 Session
Cookie 正常发送。源码目录已挂载到容器，前后端均支持热更新。
PostgreSQL 和 Redis 默认分别暴露 `5432`、`6379` 端口，方便本地调试；可以通过
`POSTGRES_PORT`、`REDIS_PORT` 修改宿主机端口。

Mock 登录预置用户及对应提现订单如下：

- UID `10001`（模拟用户 Alice）：订单 `WD-10001`。
- UID `10002`（模拟用户 Bob）：订单 `WD-10002`。

登录后可以在客服对话中输入“查询 WD-10001”。包含明确 Mock 订单号的消息会
直接查询当前 UID 的业务数据，不经过大模型；提现状态问题未提供订单号时会提示
用户补充订单号。其他问题在配置模型 Key 后使用知识库 RAG。

Mock 登录只用于验证客服业务查询流程，不应在生产环境开启。

停止服务：

```bash
docker compose down
```

已有开发数据库从匿名会话升级为用户会话时，需要显式重建会话表：

```bash
docker compose run --rm backend uv run python -m script.init_database --reset-conversations
```

该参数会删除现有会话和消息，只用于已确认可清理数据的开发环境。

彻底清空所有本地数据库和 Session 时，先停止服务，再删除本地数据目录：

```bash
docker compose down
rm -rf .local-data
```

该操作不可恢复，普通停止服务不要删除 `.local-data`。

需要构建知识索引时执行：

```bash
docker compose run --rm backend uv run python -m script.build_knowledge_index --limit 3
```

## 后端开发

```bash
cd backend
uv sync --dev
export MOCK_AUTH_ENABLED=true
export REDIS_URL='redis://localhost:6379/0'
export DATABASE_URL='postgresql+asyncpg://customer_service:customer_service_dev@localhost:5432/smart_customer_service'
export SESSION_COOKIE_SECURE=false
uv run uvicorn customer_service.main:app --reload
```

服务启动后可访问 `GET /health` 检查运行状态。需要本地调试 RAG 时，启动前额外
设置 `DASHSCOPE_API_KEY`。

运行测试：

```bash
cd backend
uv run pytest
```

## 构建知识检索库

知识库使用 PostgreSQL、pgvector 和阿里云百炼 `text-embedding-v4`（1024 维）。
数据库用户需要具备创建 `vector` 扩展和数据表的权限。

```bash
cd backend
export DATABASE_URL='postgresql+asyncpg://用户名:密码@主机:5432/数据库名'
export DASHSCOPE_API_KEY='百炼 API Key'
uv run python -m script.build_knowledge_index
```

构建脚本根据文章内容哈希跳过未变化文档。首次验证可以添加 `--limit 3`，
确认无误后再执行全量构建。不要把数据库密码或 API Key 提交到仓库。

使用内部命令验证语义检索结果：

```bash
uv run python -m script.search_knowledge '提现已经完成但钱包没有到账' --limit 5
```

## 功能规划

1. 会话与消息管理。
2. 客服知识库管理。
3. RAG 检索与智能问答。
4. AI 模型适配与回答安全控制。
5. 用户身份校验与交易所业务查询。
6. 转人工与工单流转。
7. 权限、敏感信息保护与审计。
8. 客服管理后台。
9. React 客服工作台与用户聊天界面。

功能将按需求逐步实现，不提前创建空的业务分层。
