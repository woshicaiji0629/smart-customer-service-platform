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
