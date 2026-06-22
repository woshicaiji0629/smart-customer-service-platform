# Smart Customer Service Platform

面向交易所客服场景的智能客服平台。项目主线是打通前后端客服闭环，提供流畅的聊天交互，并通过意图识别、实体抽取、业务查询模拟和 RAG 知识检索，尽可能用自动化方式处理常见问题，减少人工客服工作量。

对于无法接入实际业务环境的部分，项目使用 Mock 数据和模拟流程完成演示，例如提现订单、充值记录、身份认证审核状态和人工兜底候选。

本仓库采用前后端一体化管理：

- 后端：FastAPI、PostgreSQL、pgvector、Redis、DashScope 兼容模型 API。
- 前端：React、TypeScript、Vite。
- 本地环境：Docker Compose 一键启动前端、后端、PostgreSQL/pgvector 和 Redis。

## 核心能力

- **会话管理**：支持创建会话、发送消息、查看历史和会话列表。
- **前端交互闭环**：提供 Mock 登录、会话侧边栏、聊天工作台、历史加载和下一步提示。
- **意图识别**：规则优先、模型兜底，覆盖提现、充值、身份认证、账户安全、现货交易、人工客服和域外问题。
- **实体抽取**：识别提现订单号、充值 TxID、币种、网络、时间等关键字段。
- **Mock 业务查询优先**：用户提供明确订单号或 TxID 时，优先查询模拟业务状态，避免大模型猜测订单结果。
- **知识库 RAG**：基于 PostgreSQL + pgvector 的语义检索，结合大模型生成知识库回答。
- **回答安全控制**：RAG 回答包含引用校验和 grounding review，减少资料外扩展。
- **多轮补问**：通过结构化 `next_action` 告诉前端下一步需要用户补充什么。
- **人工客服处理**：用户明确要求人工客服时，直接返回官方客服入口引导，不受知识库覆盖率影响。
- **运营观测**：记录意图 trace 和模型用量，支持后续评估、复盘和成本分析。

## 系统架构

```text
用户
  |
  v
React 客服工作台
  |
  v
FastAPI API
  |
  +-- Auth / Redis Session
  +-- Conversation Service
        |
        +-- Intent Service
        +-- Entity Extraction
        +-- Business Query
        +-- Knowledge RAG
        +-- Answer Polisher
        +-- Next Action / Trace
  |
  v
PostgreSQL / pgvector / Redis / DashScope
```

更完整的架构和模块说明见 [docs/project-overview.md](docs/project-overview.md)。

## 适用场景示例

登录 Mock 用户后，可以在客服对话中尝试：

```text
查询 WD-10001
充值 TX-10001 没到账
提现被风控卡住了
提现已经上链但钱包没到账
我的身份认证失败了
我要人工客服
```

包含明确 Mock 订单号或 TxID 的问题会直接查询当前登录用户的业务数据；知识类问题在配置模型 Key 后进入知识库 RAG。

## 快速开始

启动本地开发环境：

```bash
docker compose up --build
```

访问地址：

```text
前端：http://localhost:5173
后端：http://localhost:8000
API 文档：http://localhost:8000/docs
```

Mock 登录、会话和业务查询无需模型 Key。需要使用知识库检索和 RAG 问答时，配置百炼 API Key：

```bash
export DASHSCOPE_API_KEY='百炼 API Key'
docker compose up --build
```

也可以将环境变量写入仓库根目录下不会提交到 Git 的 `.env` 文件。

停止服务：

```bash
docker compose down
```

## Mock 用户

本地开发预置两个 Mock 用户：

| UID | 用户 | 示例提现订单 |
| --- | --- | --- |
| `10001` | 模拟用户 Alice | `WD-10001` |
| `10002` | 模拟用户 Bob | `WD-10002` |

Mock 登录用于验证客服业务查询流程。

## 目录结构

```text
.
├── backend/        FastAPI 后端服务
├── frontend/       React 前端应用
├── docs/           项目文档
├── compose.yaml    本地 Docker Compose 编排
├── Dockerfile      前后端开发镜像
└── README.md       项目首页说明
```

后端主要模块：

```text
backend/src/customer_service/
├── auth/           Mock 登录和 Session
├── business/       Mock 提现、充值业务查询
├── conversations/  会话编排、状态流转、回答润色
├── entities/       实体抽取
├── intents/        意图识别
├── knowledge/      知识库、向量检索、RAG
├── model_usage/    模型用量统计
├── ops/            对话 trace 统计
└── main.py         FastAPI 应用入口
```

## 本地后端开发

如果不使用 Docker Compose，可以手动启动后端：

```bash
cd backend
uv sync --dev
export MOCK_AUTH_ENABLED=true
export REDIS_URL='redis://localhost:6379/0'
export DATABASE_URL='postgresql+asyncpg://customer_service:customer_service_dev@localhost:5432/smart_customer_service'
export SESSION_COOKIE_SECURE=false
uv run uvicorn customer_service.main:app --reload
```

服务启动后可访问：

```bash
curl http://localhost:8000/health
```

## 知识库索引

构建少量知识库索引用于验证：

```bash
docker compose run --rm backend uv run python -m script.build_knowledge_index --limit 3
```

验证语义检索：

```bash
cd backend
uv run python -m script.search_knowledge '提现已经完成但钱包没有到账' --limit 5
```

详细说明见 [docs/project-overview.md](docs/project-overview.md) 和 [docs/postgresql-local-setup.md](docs/postgresql-local-setup.md)。

## 测试

后端测试：

```bash
cd backend
uv run pytest
```

对话流评估：

```bash
cd backend
uv run python -m script.evaluate_conversation_flows
```

## 文档

- [技术说明](docs/project-overview.md)：模块职责、处理链路、业务边界、接口字段和扩展点。
- [PostgreSQL 本地开发指南](docs/postgresql-local-setup.md)：本地 PostgreSQL 和 pgvector 安装、初始化和排障。
- [智能客服 Agent 架构演进路线](docs/customer-service-agent-roadmap.md)：后续能力演进和优化方向。

## 当前限制

- 业务查询使用 Mock 数据，用于演示客服链路和架构设计。
- 人工客服目前是入口引导和兜底候选标记，不包含工单流转。
- RAG 回答依赖知识库覆盖率，资料缺失时只能明确说明无法确认。
- 对话状态当前为轻量 `next_action`，尚未演进为完整持久化状态机。
- Mock 登录用于本地开发和演示。
