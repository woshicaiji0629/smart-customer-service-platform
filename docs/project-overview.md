# 智能客服平台技术说明

README 负责说明项目是什么、如何快速启动以及主要能力。本文档侧重内部实现：模块职责、核心链路、业务边界、数据结构、接口字段和后续可扩展点。

## 1. 设计边界

项目当前阶段不是通用聊天机器人，也不是对接平台实际业务系统的客服。主线是构建一个可演示、可评估的智能客服闭环：前端交互流畅，后端能识别用户意图，知识类问题能检索回答，确定性业务问题用 Mock 数据模拟处理，最终目标是尽可能用自动化方式减少人工客服工作量。

核心设计边界如下：

- **前后端闭环优先**：优先保证 Mock 登录、会话列表、聊天工作台、消息发送、历史加载和下一步提示完整可用。
- **业务查询优先**：订单号、TxID 等确定性问题优先走 Mock 业务查询，不让大模型猜状态。
- **知识问答有依据**：平台规则、操作教程、问题排查类问题进入 RAG，回答必须基于检索资料。
- **人工客服不走 RAG**：用户明确要求人工客服时直接给出客服入口引导，避免知识库缺失导致反直觉回答。
- **可模拟则模拟**：无法接入实际环境的能力，用 Mock 数据和模拟状态表达，例如提现状态、充值状态、身份认证审核状态、人工兜底候选。
- **链上与平台侧分离**：提现已上链后的链上状态通常透明；当前 Mock 流程重点模拟平台侧审核、风控、合规、安全限制等未放行状态。
- **结构化状态驱动前端**：助手文案和下一步动作分离，前端依赖 `next_action` 判断该提示什么。
- **可观测优先**：意图来源、路由、处理结果、模型用量都需要记录，便于后续复盘。

## 2. 请求处理链路

用户发送消息后的服务端处理链路：

```text
POST /conversations/{conversation_id}/messages
  |
  v
ConversationService.send_message
  |
  +-- 校验会话归属和消息长度
  +-- 读取最近历史消息
  +-- IntentService.recognize
  |     +-- 规则识别
  |     +-- 模型分类兜底
  |     +-- 本地规则校正
  |
  +-- entities.extract_entities
  |
  +-- _answer_for_intent
        |
        +-- business_query
        +-- knowledge_rag
        +-- human_request
        +-- unknown
        +-- out_of_scope
  |
  +-- 保存 user message / assistant message
  +-- 保存 conversation_turn_trace
  +-- 返回 ConversationTurnResponse
```

核心代码位置：

- `backend/src/customer_service/conversations/service.py`
- `backend/src/customer_service/intents/service.py`
- `backend/src/customer_service/entities/service.py`
- `backend/src/customer_service/knowledge/rag.py`

## 3. 后端模块职责

### 3.1 `auth`

负责本地开发用 Mock 登录：

- `GET /auth/mock-users`
- `POST /auth/mock-login`
- `GET /auth/me`
- `POST /auth/logout`

Session 存储在 Redis，Cookie 使用 `SESSION_COOKIE_NAME`。该模块用于本地开发和流程验证。

### 3.2 `business`

负责确定性业务查询，目前使用 Mock 数据：

- `GET /business/withdrawals/{order_id}`
- `GET /business/deposits/{txid}`

会话服务内部也直接复用 Mock business service，用于完成“用户在对话中输入订单号/TxID后自动查询”的流程。

### 3.3 `conversations`

负责会话主流程：

- 会话创建、列表、历史和消息发送 API。
- 根据意图结果编排业务查询、RAG、人工客服、未知问题处理。
- 生成 `next_action`。
- 保存消息 sources。
- 记录每轮 trace。
- 对身份认证失败类 RAG 回答进行可选润色。

关键文件：

- `api.py`：HTTP API 和响应模型。
- `service.py`：对话编排核心逻辑。
- `repository.py`：会话、消息和 trace 持久化。
- `polisher.py`：回答润色器。

### 3.4 `intents`

负责意图识别。当前采用规则优先、模型兜底：

- 高确定性表达由规则直接命中，例如订单号、TxID、人工客服、域外问题。
- 规则无法覆盖时调用模型分类。
- 模型结果会经过本地校正，避免泛化问题误入业务查询。

核心输出字段：

```json
{
  "route": "knowledge_rag",
  "category": "identity_verification",
  "intent": "verification_failure",
  "confidence": 0.92,
  "entities": {},
  "missing_fields": [],
  "source": "model"
}
```

### 3.5 `entities`

负责独立实体抽取，避免把字段识别散落在意图规则中。

当前覆盖：

- 提现订单号。
- 充值 TxID。
- 币种。
- 网络。
- 时间提示。

实体结果用于：

- 补充 intent decision 中缺失的字段。
- 判断是否需要补问。
- 写入 trace 便于评估。

### 3.6 `knowledge`

负责知识库处理和 RAG：

- Markdown 文档解析。
- 文档和 chunk 入库。
- embedding 生成。
- pgvector 检索。
- RAG 回答生成。
- 引用编号校验。
- grounding review。
- 模型调用用量记录。

RAG 的关键约束写在 prompt 中：

- 只能基于参考资料回答。
- 不跨场景、币种、链网络类推。
- 不补充资料中没有的链接、字段要求或安全建议。
- 每项事实需要带 `[资料 N]`。
- 资料不足时明确说明无法根据现有资料确认。

### 3.7 `ops`

负责对话 trace 汇总，便于看系统实际路由情况：

- 按 intent source 统计。
- 按 route 统计。
- 按 handling result 统计。
- 输出高频 route/category/intent/handling_result 组合。

### 3.8 `model_usage`

负责模型用量统计：

- provider。
- model。
- purpose。
- calls。
- prompt tokens。
- completion tokens。
- estimated cost。

目前模型用量由 chat 和 embedding client 写入 sink，再由 repository 汇总。

## 4. 核心业务路由

### 4.1 路由类型

当前主要 route：

| route | 说明 |
| --- | --- |
| `business_query` | 需要业务查询，例如提现订单、充值 TxID |
| `knowledge_rag` | 平台规则、教程、问题排查类知识问答 |
| `human_request` | 用户明确要求人工客服 |
| `unknown` | 无法判断用户问题 |
| `out_of_scope` | 非交易所客服范围问题 |

### 4.2 提现

提现问题以平台侧状态为主：

- 有提现订单号：查询平台侧提现记录。
- 无提现订单号但表达审核、风控、限制、卡住、不放行：补问提现订单号。
- 明确已完成、已广播、已上链：返回链上透明说明，不再要求客服排查链上状态。

设计原因：

- 上链后的链上状态通常可通过 TxID、区块浏览器、目标地址和网络自行核对。
- 当前 Mock 流程重点模拟平台是否放行、是否被审核或风控卡住。

### 4.3 充值

充值问题仍保留链上和平台入账排查：

- 有 TxID：查询充值记录。
- 无 TxID：补问充值 TxID。
- TxID 查不到：继续收集币种、网络、充值时间、页面提示，用于后续排查或人工兜底。

提现和充值不能套用同一逻辑。提现已上链通常代表平台侧已放行；充值链上成功但未入账仍可能需要平台侧排查。

### 4.4 身份认证

身份认证问题主要走知识库：

- 认证失败原因。
- 重复证件。
- 自拍或人脸识别失败。
- 地区或资格限制。
- 审核超时或邮件通知。

对 `identity_verification / verification_failure` 场景，会额外调用回答润色器。润色器只改变表达，不应新增事实；如果模型失败、返回空或丢失引用，则回退原始 RAG 答案。

### 4.5 人工客服

用户明确要求人工客服时直接返回固定话术，不调用 RAG：

```text
已记录你的人工客服诉求。请通过平台 App 或网页端的官方在线客服入口联系人工客服；也可以补充具体问题、订单号或页面提示，方便继续处理。
```

重复要求人工客服时，标记为 `manual_fallback_candidate`。

## 5. `next_action` 设计

助手消息可以携带 `next_action`，用于结构化表达下一步需要用户补充什么。

字段：

| 字段 | 说明 |
| --- | --- |
| `type` | 前端可识别的动作类型 |
| `state` | 当前对话状态 |
| `expected_input` | 期望用户输入的字段类型 |
| `missing_fields` | 缺失字段列表 |
| `manual_fallback_candidate` | 是否进入人工兜底候选 |

示例：

```json
{
  "type": "provide_withdrawal_order_id",
  "state": "awaiting_withdrawal_order_id",
  "expected_input": "withdrawal_order_id",
  "missing_fields": ["order_id"],
  "manual_fallback_candidate": false
}
```

当前常见状态：

- `awaiting_withdrawal_order_id`
- `awaiting_deposit_txid`
- `awaiting_deposit_followup_details`
- `awaiting_problem_description`
- `manual_fallback_candidate`

当前状态是服务层即时计算结果，尚未作为完整状态机持久化。

目标状态机可以按任务型客服流程收敛为：

| 状态 | 含义 | 典型场景 |
| --- | --- | --- |
| `greeting` | 新会话或无明确任务 | 用户刚进入客服 |
| `info_gathering` | 正在收集必要字段 | 缺订单号、TxID、币种、网络、时间 |
| `processing` | 正在查询或生成回答 | 业务查询、RAG 回答、模型润色 |
| `confirmation` | 需要用户确认或补充 | 查不到记录、信息不完整、结果不确定 |
| `completed` | 当前任务已结束 | 已回答、已查询、已给出明确下一步 |
| `failed` | 当前自动处理失败 | 多次 unknown、模型失败、Mock 服务不可用 |
| `manual_fallback_candidate` | 建议人工兜底 | 重复人工诉求、多次无法解决 |

目标状态转换：

```text
greeting
  -> info_gathering
  -> processing
  -> completed

processing
  -> confirmation
  -> info_gathering

processing
  -> failed
  -> manual_fallback_candidate
```

短期仍保留 `next_action` 作为 API 输出；中期再把状态持久化到会话任务表，避免复杂多轮对话只依赖最近历史判断。

## 6. 连续上下文意图识别与自动处理

这是下一阶段需要做扎实的核心能力。用户在日常客服对话中不会每一句都完整描述问题，经常会连续补充、追问、抱怨、换话题或要求人工客服。系统需要结合当前会话状态持续判断用户意图，并在条件满足时自动调用后端能力。

目标链路：

```text
用户连续输入
  |
  v
结合当前状态理解输入类型
  |
  +-- 补充字段
  +-- 继续追问
  +-- 换话题
  +-- 新问题
  +-- 要人工客服
  |
  v
更新意图、实体和任务状态
  |
  v
判断是否满足自动处理条件
  |
  +-- 满足：调用 Mock 业务查询或 RAG
  +-- 不满足：只补问缺失字段
  +-- 多次失败：进入兜底候选
  |
  v
生成回答、next_action 和 trace
```

### 6.1 输入类型判断

连续对话中，当前用户输入至少需要区分以下类型：

| 类型 | 示例 | 处理方式 |
| --- | --- | --- |
| 补充字段 | `WD-10001`、`TX-10001`、`TRC20` | 合并到当前任务实体，满足条件后自动查询 |
| 继续追问 | `还是没到账`、`那怎么办` | 继承当前任务和上下文继续处理 |
| 换话题 | `身份认证失败了` | 结束或挂起旧任务，切换到新意图 |
| 新问题 | `限价单为什么没成交` | 重新识别意图并进入知识库或业务流程 |
| 明确人工 | `我要人工客服` | 直接返回固定客服入口引导 |
| 不满意反馈 | `没解决`、`你没看懂` | 记录未解决，必要时进入兜底候选 |

### 6.2 内部状态字段

当前 API 已返回 `next_action`，但内部还需要更稳定的任务上下文。短期可以从最近历史和 trace 推导，中期可以持久化为任务状态。

建议字段：

```json
{
  "current_task": "withdrawal_status",
  "current_route": "business_query",
  "current_category": "withdrawal",
  "current_intent": "status_query",
  "collected_entities": {
    "order_id": "WD-10001"
  },
  "missing_fields": [],
  "last_handling_result": "withdrawal_order_missing",
  "retry_count": 1,
  "manual_fallback_candidate": false
}
```

这些字段用于判断：

- 用户这句话是否是在补上一轮缺失字段。
- 是否已经满足自动调用 Mock 后端能力的条件。
- 是否应该切换到新任务。
- 是否已经多次无法解决，需要兜底。

### 6.3 自动调用后端能力

系统不应让用户重复描述已经提供过的信息。只要字段满足，就自动调用对应能力。

| 场景 | 自动处理条件 | 调用能力 |
| --- | --- | --- |
| 提现状态 | category=`withdrawal` 且有 `order_id` | Mock withdrawal lookup |
| 充值状态 | category=`deposit` 且有 `txid` | Mock deposit lookup |
| 身份认证失败 | category=`identity_verification` | RAG + 可选回答润色 |
| 现货交易规则 | category=`spot_trading` | RAG |
| 人工客服 | route=`human_request` | 固定客服入口引导 |
| 域外问题 | route=`out_of_scope` | 范围限制提示 |

### 6.4 典型连续对话

提现补字段：

```text
用户：提现没到账
系统：请提供提现订单号
用户：WD-10001
系统：自动识别为上一轮提现任务的订单号，并查询 Mock 提现状态
```

充值补字段：

```text
用户：充值没到账
系统：请提供充值 TxID
用户：TX-10001
系统：自动识别为上一轮充值任务的 TxID，并查询 Mock 充值状态
```

身份认证追问：

```text
用户：身份认证失败
系统：解释常见失败原因
用户：我的身份证没问题
系统：继承身份认证失败上下文，继续解释可能是照片、自拍、地区、重复证件等环节问题
```

换话题：

```text
用户：提现没到账
系统：请提供提现订单号
用户：身份认证失败了
系统：识别为新任务，切换到身份认证问题，不继续追问提现订单号
```

人工客服：

```text
用户：我要人工客服
系统：直接返回官方客服入口引导，不走 RAG
```

### 6.5 兜底策略

以下情况应进入兜底候选或明确提示人工客服入口：

- 多次 unknown。
- 多次补字段失败。
- 用户明确表示不满意或没有解决。
- 用户明确要求人工客服。
- Mock 服务无法返回有效结果，且用户继续追问。

兜底不等于项目要接入实际客服系统；当前阶段可以先用模拟状态和 trace 记录，后续再做模拟工单闭环。

## 7. 输入预处理与回复后处理

### 7.1 输入预处理

当前输入处理分散在 API 校验、意图识别和实体抽取中。后续可以抽出独立的 TextPreprocessor，统一做轻量标准化。

建议职责：

- 清理首尾空白、重复空格、不可见字符。
- 保留用户原文，标准化文本只用于识别和查询。
- 对订单号、TxID、币种、网络做大小写和别名归一。
- 对常见链名做标准化，例如 TRC20、TRON、ERC20、BEP20。
- 对纯数字输入按上下文处理，只在 pending 状态下尝试补全订单前缀。
- 标记疑似敏感信息，后续可用于演示 prompt 脱敏策略。

建议输出：

```json
{
  "raw_text": "提现  wd-10001  怎么还没到",
  "normalized_text": "提现 WD-10001 怎么还没到",
  "normalization_notes": ["trimmed_spaces", "uppercased_order_id"]
}
```

### 7.2 回复后处理

RAG 内部使用 `[资料 N]` 是为了引用校验和防幻觉，但用户侧直接展示会显得机械。后续应增加展示层后处理：

- 用户正文移除 `[资料 N]`。
- sources 继续保存和返回，用于后台溯源、质检和调试。
- 可选在前端折叠展示“参考来源”，而不是把引用编号插入正文。
- 保留内部原始回答，便于排查模型引用和 grounding review。

短期推荐策略：

```text
RAG 原始回答 -> 引用校验 -> grounding review -> 保存 sources -> 展示层清洗引用 -> 返回用户
```

## 8. RAG 生成链路

RAG 服务入口：

```text
RagService.answer(question, history, category)
```

处理步骤：

1. 清理用户问题。
2. 如果存在历史追问，先调用模型将问题改写为独立检索问题。
3. 调用 KnowledgeSearchService 做向量检索。
4. 按 article 去重并组织参考资料。
5. 调用 chat client 生成草稿回答。
6. 校验引用编号是否越界。
7. 必要时要求模型修正无效引用。
8. 调用 grounding review 删除资料不支持内容。
9. 再次校验引用编号。
10. 返回最终回答和 sources。

失败策略：

- 检索结果为空或低于阈值：返回资料不足回答。
- 模型失败：API 层返回 502。
- 引用校验失败：API 层返回 502。
- RAG 未配置：知识问答相关接口返回 503。

### 8.1 目标混合检索

当前主要使用向量检索和类目过滤。后续适合补充三路召回：

```text
用户 query
  |
  +-- 向量检索：语义相近问题
  +-- 关键词检索：错误码、字段名、产品名、链名
  +-- 结构化过滤：category、tag、业务线、文档类型
  |
  v
结果融合排序
  |
  v
RAG 生成与引用校验
```

融合排序可参考以下因子：

- 向量相似度。
- 关键词命中数量和位置。
- 意图 category 是否匹配。
- 文档更新时间和有效性。
- 历史命中质量。

这个方向适合解决短 query、专有名词、错误码、链名、活动名称等向量检索容易漏召回的问题。

## 9. 回答润色器

回答润色器位于：

```text
backend/src/customer_service/conversations/polisher.py
```

当前只在身份认证失败类问题中启用。

设计约束：

- 只润色表达，不新增事实、原因、时效、链接、入口或处理承诺。
- 不得删除或改错原回答中的 `[资料 N]`。
- 不得改变原回答业务含义。
- 不输出“根据资料”“官方指南整理”等生硬表述。

兜底逻辑：

- polisher 未配置：返回原答案。
- 非身份认证失败意图：返回原答案。
- 模型异常：记录日志并返回原答案。
- 模型返回空：返回原答案。
- 引用丢失：返回原答案。

## 10. 数据存储

PostgreSQL 表按职责分为几类：

| 类型 | 说明 |
| --- | --- |
| knowledge documents | 知识文档元数据 |
| knowledge chunks | 知识片段、embedding、标题、来源 |
| conversations | 会话记录 |
| messages | 用户和助手消息 |
| conversation turn traces | 每轮意图、路由、处理结果 |
| model usage logs | 模型调用和 token 用量 |

Redis 用于 Mock 登录 Session。

本地 Docker Compose 默认把数据放在：

```text
.local-data/postgres/
.local-data/redis/
```

这些目录是运行数据，不应提交到 Git。

## 11. 接口细节

### 11.1 会话接口

创建会话：

```http
POST /conversations
```

发送消息：

```http
POST /conversations/{conversation_id}/messages
```

请求：

```json
{
  "content": "查询 WD-10001"
}
```

响应结构：

```json
{
  "user_message": {
    "id": 1,
    "conversation_id": "uuid",
    "role": "user",
    "content": "查询 WD-10001",
    "sources": [],
    "created_at": "2026-06-19T08:00:00Z",
    "next_action": null
  },
  "assistant_message": {
    "id": 2,
    "conversation_id": "uuid",
    "role": "assistant",
    "content": "提现订单 WD-10001 当前状态...",
    "sources": [],
    "created_at": "2026-06-19T08:00:01Z",
    "next_action": null
  }
}
```

获取列表：

```http
GET /conversations?limit=50&cursor=...
```

获取历史：

```http
GET /conversations/{conversation_id}
```

### 11.2 知识库接口

检索：

```http
POST /knowledge/search
```

问答：

```http
POST /knowledge/answer
```

这两个接口依赖模型 Key 和知识库索引。未配置时返回 503。

### 11.3 运营接口

对话 trace 汇总：

```http
GET /ops/conversation-traces/summary
```

可用于观察：

- 规则和模型命中比例。
- 业务查询、RAG、未知问题、人工请求占比。
- 兜底候选数量。
- 高频失败路径。

### 11.4 模型用量接口

```http
GET /model-usage/summary
```

用于按 provider、model、purpose 汇总模型调用次数、token 和估算成本。

## 12. 评估与测试

后端测试入口：

```bash
cd backend
uv run pytest
```

对话流评估：

```bash
cd backend
uv run python -m script.evaluate_conversation_flows
```

当前测试覆盖：

- auth 和 Session。
- business Mock 查询。
- conversation API 和 service。
- intent classification。
- entity extraction。
- RAG 生成、引用校验和 review。
- knowledge search。
- model usage。
- ops trace summary。

评估样例位置：

```text
backend/evaluation/
├── conversation_flow_cases.json
├── intent_classification_cases.json
└── knowledge_retrieval_cases.json
```

目标评估指标：

| 维度 | 指标 | 说明 |
| --- | --- | --- |
| 意图识别 | intent accuracy | 预测意图与标注意图一致率 |
| 实体抽取 | precision / recall / F1 | 订单号、TxID、币种、网络、时间抽取质量 |
| 状态流转 | next_action accuracy | 缺字段补问和下一步动作是否正确 |
| 检索质量 | recall@k / hit rate | 正确文档是否进入 top k |
| RAG 安全 | citation validity | 引用编号是否存在且资料支持结论 |
| 业务处理 | task completion rate | 用户补充字段后是否完成业务查询或明确兜底 |
| 效率 | average response time | 平均响应时间 |
| 体验 | unresolved rate | 未解决、重复追问、人工兜底占比 |
| 成本 | cost per resolved turn | 单轮或单个解决问题的模型成本 |

## 13. 参考方案吸收情况

结合智能客服通用建设方案，本项目已经吸收或计划吸收以下能力。这里不绑定某个厂商实现，而是沉淀可落地到当前项目的设计点。

| 能力点 | 当前状态 | 当前实现或计划 |
| --- | --- | --- |
| 用户输入预处理 | 部分完成 | 已做消息 trim、长度校验和实体正则抽取；后续补充独立输入标准化层 |
| 意图识别 | 已完成主干 | 规则优先、模型兜底，本地规则校正模型输出 |
| 实体抽取 | 已完成主干 | 独立 `entities` 模块，覆盖订单号、TxID、币种、网络、时间 |
| 对话状态管理 | 部分完成 | 当前以 `next_action` 表达轻量状态，后续演进为持久化状态机 |
| 知识检索 | 已完成主干 | pgvector 向量检索 + 类目过滤；后续补关键词召回、结构化过滤和融合排序 |
| 业务查询 | 已完成演示版 | Mock 提现、充值查询已接入，可继续扩展更多模拟状态 |
| 回复生成 | 已完成主干 | RAG 生成 + 引用校验 + grounding review + 身份认证回答润色 |
| 回复后处理 | 待完成 | 用户侧清洗 `[资料 N]`，sources 保留给后台溯源 |
| 人工兜底 | 部分完成 | 人工客服固定入口引导、重复请求标记兜底候选；后续补模拟工单/客服后台 |
| 运营分析 | 部分完成 | trace 汇总、模型用量统计已具备；后续补自动解决率和失败样本复盘 |
| 评估体系 | 部分完成 | 已有意图、知识检索、对话流评估；后续补实体和状态流转指标 |
| 用户反馈闭环 | 待完成 | 后续支持点赞/点踩、未解决标记、失败样本回流 |

不适合直接落地的内容：

- 不直接引入 BERT、BiLSTM-CRF 等训练模型。当前项目数据量和目标阶段更适合规则 + LLM + 可评估测试集。
- 不直接替换为 FAISS。当前已经选择 PostgreSQL + pgvector，便于和业务数据、知识文档、运营统计统一管理。
- 不提前引入复杂微服务拆分。当前仍以单体 FastAPI 应用为主，优先保证业务链路清晰和可测试。

## 14. 当前完成度

### 14.1 已完成

- 前后端基础链路：React 工作台、FastAPI API、Docker Compose 本地环境。
- Mock 登录和 Redis Session。
- 会话创建、发送消息、历史记录和会话列表。
- 前端客服闭环：Mock 登录、会话侧边栏、聊天工作台、历史加载、消息发送和 `next_action` 提示。
- 意图识别：规则优先、模型兜底。
- 实体抽取模块：提现订单号、充值 TxID、币种、网络、时间。
- 基础连续处理：提现订单号、充值 TxID 等明确补充字段可以进入原任务继续处理。
- Mock 业务查询：提现订单、充值 TxID。
- RAG 知识库问答：文档解析、embedding、pgvector 检索、回答生成。
- RAG 安全控制：引用校验、引用修正、grounding review。
- 会话状态提示：通过 `next_action` 返回缺失字段和下一步动作。
- 提现业务边界：区分平台侧审核/风控与链上透明状态。
- 人工客服请求：直接固定客服入口引导，不再走 RAG。
- 身份认证失败回答润色：失败或引用丢失时回退原答案。
- 模型用量统计：按 provider、model、purpose 汇总 token 和成本。
- 运营 trace：记录 route、category、intent、handling_result、intent_source。
- 后端测试和对话流评估。

### 14.2 部分完成

- 多轮任务流：已有缺字段补问和补充信息处理，但还不是完整状态机。
- 连续上下文理解：能处理部分补字段场景，但对追问、换话题、不满意反馈的系统性覆盖还不够。
- 人工兜底：已有候选标记和入口引导，但还没有模拟工单闭环。
- 业务查询：当前是 Mock 提现和充值服务，身份认证审核状态仍是知识库解释。
- 知识检索：已支持向量检索和类目过滤，尚未做关键词召回和融合排序。
- 评估体系：已有主干测试，缺少实体抽取 F1、状态流转准确率、更贴近日常客服的追问样本。
- 用户侧展示：RAG 内部引用可校验，但正文 `[资料 N]` 对用户仍偏技术化。

### 14.3 未完成

- 更完整的 Mock 业务状态和模拟客服后台。
- 模拟工单流转。
- 持久化对话状态机。
- 演示级用户隔离、日志和脱敏策略。
- 客服运营后台页面。
- 从本地 trace 自动沉淀评估样本的闭环。

## 15. 当前问题

1. **业务数据仍是 Mock**
   当前可以验证流程，但提现、充值和身份认证状态都来自模拟数据或知识库，不能代表平台实际处理结果。

2. **连续上下文处理还不够扎实**
   `next_action` 能表达下一步，但系统还缺少更稳定的内部任务状态。复杂多轮任务、用户跳话题、返回旧问题、表达不满意时，后续需要持续识别用户是在补字段、追问、换话题还是新问题。

3. **评估集覆盖还不够贴近日常使用**
   当前评估覆盖主干路径，缺少更贴近日常客服的表达，例如抱怨、反问、连续追问、跳话题、只发截图描述、口语化短句等。

4. **RAG 展示层还不够自然**
   RAG 内部要求 `[资料 N]` 是为了校验和防幻觉，但直接展示给用户会显得机械。后续应保留 sources，用户正文去掉引用编号。

5. **人工兜底没有模拟闭环**
   系统可以标记兜底候选，但还没有模拟创建工单、分配客服、记录处理进度。

6. **身份认证缺少模拟审核状态**
   当前只能根据知识库解释常见失败原因，还没有 Mock 审核状态用于演示“失败原因查询”。

7. **知识检索召回方式单一**
   目前以向量检索为主，遇到精确术语、错误码、特殊字段时，可能需要关键词召回补充。

## 16. 后续迭代计划

### 阶段 1：前后端体验闭环

目标：把客服工作台体验打磨顺，让用户能完整、流畅地走完登录、建会话、提问、补充字段、查看回答和继续追问。

- 优化聊天工作台加载态、空态、错误态和消息发送反馈。
- 优化会话列表、历史加载、会话切换和旧会话恢复体验。
- 让 `next_action` 在前端有清晰提示，例如补订单号、补 TxID、补页面提示。
- 去除用户正文中的 `[资料 N]`，sources 继续保留给后台溯源。
- 补充身份认证失败、提现风控、充值未到账、人工客服、未知问题的高频样例。
- 优化重复追问、抱怨、人工客服诉求的话术。
- 增加回答风格规则，避免“根据参考资料”“无法确认如何联系客服”等机械表达。
- 扩充对话流评估集，覆盖多轮追问和跳话题。

验收标准：

- 用户侧回复不再暴露内部引用编号。
- 前端交互能覆盖登录、创建会话、发送消息、补充字段、切换会话和查看历史。
- 人工客服请求稳定返回固定入口引导。
- 身份认证失败类回答更自然，且不新增知识库外事实。
- 对话流评估覆盖 30-50 条核心样例。

### 阶段 2：状态流转增强

目标：围绕连续上下文意图识别和实体抽取，把轻量 `next_action` 演进为更稳定的多轮任务流。

- 定义任务状态：待补字段、处理中、已完成、无法处理、兜底候选。
- 固化“缺字段 -> 补问 -> 用户补充 -> 查询 -> 完成/兜底”的流程。
- 增加输入类型判断：补字段、继续追问、换话题、新问题、人工客服、不满意反馈。
- 满足字段条件时自动调用 Mock 后端能力，避免用户重复描述。
- 支持用户补充纯数字订单片段时，在 pending 场景安全补全。
- 增加状态流转评估指标，例如 next action 准确率、任务完成率、补问后完成率。

验收标准：

- 提现、充值、身份认证、未知问题、人工客服都有明确状态。
- 前端提示完全由结构化状态驱动。
- 补字段、追问、换话题和人工客服请求都有覆盖样例。
- 状态流转有独立评估用例。

### 阶段 3：Mock 业务能力扩展

目标：继续用模拟数据完善客服业务链路，让演示更完整。

- 扩展 Mock 提现订单状态，例如审核中、风控中、已拒绝、已放行、已上链。
- 扩展 Mock 充值状态，例如链上确认中、平台入账中、网络不匹配、缺少 Memo。
- 增加 Mock 身份认证审核状态，例如照片不清晰、证件重复、地区限制、审核中。
- 明确演示级用户隔离、日志和数据脱敏边界。
- 区分用户可见回答和模拟客服排查信息。

验收标准：

- 用户只能查询当前 Mock 用户名下的数据。
- 查询失败、超时、无匹配数据都有明确错误处理。
- 演示数据不会把敏感字段传入模型 prompt 或对外回答。

### 阶段 4：模拟人工兜底闭环

目标：让“兜底候选”进入可演示的客服处理流程。

- 增加模拟工单表或客服后台页面。
- 保存用户问题、上下文、实体、trace、业务查询结果。
- 支持模拟人工处理状态回写。
- 运营侧查看高频兜底原因和失败样本。

验收标准：

- 重复无法解决或明确人工诉求可以创建模拟工单。
- 模拟工单包含足够上下文，不需要客服重新询问基础信息。
- 可以按意图和处理结果统计兜底原因。

### 阶段 5：检索与评估优化

目标：提升知识类问题命中率和可评估性。

- 增加关键词召回，补充向量检索对错误码、专有名词、短 query 的不足。
- 做向量分、关键词分、类目匹配分融合排序。
- 将 trace 中的 unknown、兜底候选、低置信问题回流为评估样本。
- 增加实体抽取 Precision / Recall / F1。
- 增加 RAG 命中率、引用有效率、资料不足率。

验收标准：

- 高频知识问题能稳定命中正确文章。
- 无关问题不会强行生成知识库答案。
- 评估报告能同时覆盖意图、实体、状态、检索、RAG 回答质量。

### 阶段 6：运营后台与成本治理

目标：让系统可运营、可复盘、可控成本。

- 展示自动解决率、人工兜底率、unknown 占比。
- 展示模型调用次数、token 和估算成本。
- 展示高频失败问题和高频知识缺口。
- 支持用户反馈：已解决、未解决、点赞、点踩、转人工原因。
- 将用户反馈、trace、低置信样本回流到评估集和知识库迭代。
- 对常见知识问题考虑缓存。
- 对意图识别和实体抽取进行并行化，降低平均响应时间。
- 对业务查询、RAG 检索、模型调用分别设置超时、重试和降级策略。
- 对模型调用失败、RAG 检索失败、业务查询失败分别统计。

验收标准：

- 运营人员可以看到系统解决了什么、没解决什么、成本花在哪里。
- 失败样本可以进入下一轮知识库和评估集迭代。
- 高频问题具备缓存或降级策略，避免模型调用成本无序增长。
