# Smart Customer Service Platform

交易所智能客服平台，前后端使用同一个仓库管理。

## 目录

- `backend/`：Python 后端，使用 FastAPI 和 uv。
- `frontend/`：React 前端，将在后续阶段初始化。

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
