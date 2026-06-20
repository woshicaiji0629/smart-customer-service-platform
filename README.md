# Smart Customer Service Platform

交易所智能客服平台，前后端使用同一个仓库管理。

## 目录

- `backend/`：Python 后端，使用 FastAPI 和 uv。
- `frontend/`：React、TypeScript 和 Vite 前端。

## Docker Compose 本地开发

Docker Compose 同时启动前端和后端，并复用本机运行的 PostgreSQL。
启动前，在当前终端设置后端所需的环境变量：

```bash
export DATABASE_URL='postgresql+asyncpg://customer_service:本地开发密码@localhost:5432/smart_customer_service'
export DASHSCOPE_API_KEY='百炼 API Key'
```

Docker Compose 只会读取执行启动命令时所在终端的环境变量。也可以把这些变量
保存在仓库根目录中不会提交到 Git 的 `.env` 文件里。后端容器启动时会自动将
数据库地址中的 `localhost` 或 `127.0.0.1` 转换为 `host.docker.internal`，
不会修改宿主机中的环境变量。

启动服务：

```bash
docker compose up --build
```

前端地址为 `http://localhost:5173`，后端 API 文档地址为
`http://127.0.0.1:8000/docs`。源码目录已挂载到容器，前后端均支持热更新。

停止服务：

```bash
docker compose down
```

数据库表尚未初始化时执行：

```bash
docker compose run --rm backend uv run python -m script.init_database
```

需要构建知识索引时执行：

```bash
docker compose run --rm backend uv run python -m script.build_knowledge_index --limit 3
```

## 后端开发

```bash
cd backend
uv sync --dev
uv run uvicorn customer_service.main:app --reload
```

服务启动后可访问 `GET /health` 检查运行状态。

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
