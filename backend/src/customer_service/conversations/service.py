"""Conversation application service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from customer_service.business.service import (
    DepositLookup,
    DepositRecord,
    MOCK_DEPOSIT_SERVICE,
    WithdrawalLookup,
    WithdrawalRecord,
    extract_deposit_txid,
)
from customer_service.conversations.repository import (
    ConversationCursor,
    ConversationHistory,
    ConversationNotFoundError,
    ConversationPage,
    ConversationRecord,
    ConversationRepository,
    ConversationTurn,
)
from customer_service.knowledge.rag import RagAnswer, RagHistoryMessage, RagService
from customer_service.intents.service import (
    IntentDecision,
    IntentHistoryMessage,
    IntentRecognizer,
)


MAX_MESSAGE_LENGTH = 4_000
MAX_HISTORY_MESSAGES = 6
INACTIVE_CONVERSATION_CLOSE_AFTER_SECONDS = 300
DEFAULT_CONVERSATION_LIST_LIMIT = 50
MAX_CONVERSATION_LIST_LIMIT = 100
WITHDRAWAL_ORDER_ID_PROMPT = (
    "请提供提现订单号，例如 WD-10001，我可以帮你查询处理状态。"
)
DEPOSIT_TXID_PROMPT = "请提供充值 TxID，例如 TX-10001，我可以帮你查询充值处理状态。"
UNKNOWN_INTENT_PROMPT = "请补充说明你遇到的具体问题、操作步骤或页面提示。"
HUMAN_REQUEST_PROMPT = (
    "请先描述需要解决的具体问题，我会优先尝试自动查询或提供处理方案。"
)
OUT_OF_SCOPE_ANSWER = "我目前只能处理交易所账户、交易和平台使用相关的问题。"
INACTIVE_CONVERSATION_NOTICE = (
    "由于你超过 5 分钟未回复，之前的问题已自动关闭。"
    "我们将按新的问题重新处理。"
)


class ConversationService:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        rag_service: RagService | None,
        withdrawal_service: WithdrawalLookup,
        intent_service: IntentRecognizer,
        deposit_service: DepositLookup | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._rag_service = rag_service
        self._deposit_service = deposit_service or MOCK_DEPOSIT_SERVICE
        self._withdrawal_service = withdrawal_service
        self._intent_service = intent_service
        self._now = now or (lambda: datetime.now(UTC))

    async def create_conversation(self, user_id: str) -> ConversationRecord:
        return await self._repository.create_conversation(user_id)

    async def list_conversations(
        self,
        user_id: str,
        *,
        limit: int = DEFAULT_CONVERSATION_LIST_LIMIT,
        cursor: ConversationCursor | None = None,
    ) -> ConversationPage:
        if limit > MAX_CONVERSATION_LIST_LIMIT:
            raise ValueError(f"limit 不能超过 {MAX_CONVERSATION_LIST_LIMIT}")
        return await self._repository.list_conversations(
            user_id,
            limit=limit,
            cursor=cursor,
        )

    async def send_message(
        self,
        user_id: str,
        conversation_id: UUID,
        content: str,
    ) -> ConversationTurn:
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("content 不能为空")
        if len(normalized_content) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"content 不能超过 {MAX_MESSAGE_LENGTH} 个字符")
        if not await self._repository.conversation_exists(
            conversation_id,
            user_id,
        ):
            raise ConversationNotFoundError(str(conversation_id))

        recent_messages = await self._repository.get_recent_messages(
            conversation_id,
            user_id=user_id,
            limit=MAX_HISTORY_MESSAGES,
        )
        is_inactive = _is_inactive_context(recent_messages, now=self._now())
        active_messages = [] if is_inactive else recent_messages
        intent_history = [
            IntentHistoryMessage(role=message.role, content=message.content)
            for message in active_messages
        ]
        decision = await self._intent_service.recognize(
            normalized_content,
            history=intent_history,
        )
        decision = _apply_pending_intent(decision, active_messages)
        rag_history = [
            RagHistoryMessage(role=message.role, content=message.content)
            for message in active_messages
        ]
        answer = await self._answer_for_intent(
            user_id,
            normalized_content,
            decision,
            rag_history,
        )
        if is_inactive:
            answer = RagAnswer(
                answer=f"{INACTIVE_CONVERSATION_NOTICE}\n\n{answer.answer}",
                sources=answer.sources,
            )
        sources = [
            {
                "article_id": source.article_id,
                "title": source.title,
                "source_url": source.source_url,
            }
            for source in answer.sources
        ]
        return await self._repository.save_turn(
            conversation_id=conversation_id,
            user_id=user_id,
            user_content=normalized_content,
            assistant_content=answer.answer,
            assistant_sources=sources,
        )

    async def _answer_for_intent(
        self,
        user_id: str,
        content: str,
        decision: IntentDecision,
        history: list[RagHistoryMessage],
    ) -> RagAnswer:
        if decision.route == "business_query" and decision.topic == "withdrawal":
            order_id = decision.entities.get("order_id")
            if not order_id or "order_id" in decision.missing_fields:
                return RagAnswer(answer=WITHDRAWAL_ORDER_ID_PROMPT, sources=[])
            withdrawal = self._withdrawal_service.get_withdrawal(user_id, order_id)
            return RagAnswer(answer=_withdrawal_answer(order_id, withdrawal), sources=[])
        if decision.topic == "deposit":
            txid = decision.entities.get("txid") or extract_deposit_txid(content)
            if not txid:
                return RagAnswer(answer=DEPOSIT_TXID_PROMPT, sources=[])
            deposit = self._deposit_service.get_deposit(user_id, txid)
            return RagAnswer(answer=_deposit_answer(txid, deposit), sources=[])
        if decision.route == "out_of_scope":
            return RagAnswer(answer=OUT_OF_SCOPE_ANSWER, sources=[])
        if decision.route == "human_request":
            return RagAnswer(answer=HUMAN_REQUEST_PROMPT, sources=[])
        if decision.route == "unknown":
            return RagAnswer(answer=UNKNOWN_INTENT_PROMPT, sources=[])
        if self._rag_service is None:
            raise RagUnavailableError
        return await self._rag_service.answer(content, history=history)

    async def get_history(
        self,
        user_id: str,
        conversation_id: UUID,
    ) -> ConversationHistory:
        return await self._repository.get_history(conversation_id, user_id)


class RagUnavailableError(RuntimeError):
    """Raised when a conversation requires RAG but no model key is configured."""


def _is_inactive_context(
    history: list[MessageRecord],
    *,
    now: datetime,
) -> bool:
    if not history:
        return False
    latest_message = history[-1]
    return (
        now - latest_message.created_at
    ).total_seconds() > INACTIVE_CONVERSATION_CLOSE_AFTER_SECONDS


def _apply_pending_intent(
    decision: IntentDecision,
    history: list[MessageRecord],
) -> IntentDecision:
    if decision.route != "unknown":
        return decision
    pending_topic = _pending_topic_from_history(history)
    if pending_topic == "deposit":
        return IntentDecision(
            route="business_query",
            topic="deposit",
            confidence=1.0,
            entities={},
            missing_fields=("txid",),
        )
    if pending_topic == "withdrawal":
        return IntentDecision(
            route="business_query",
            topic="withdrawal",
            confidence=1.0,
            entities={},
            missing_fields=("order_id",),
        )
    return decision


def _pending_topic_from_history(history: list[MessageRecord]) -> str | None:
    for message in reversed(history):
        role = getattr(message, "role", None)
        content = getattr(message, "content", "")
        if role != "assistant" or not isinstance(content, str):
            continue
        if DEPOSIT_TXID_PROMPT in content:
            return "deposit"
        if WITHDRAWAL_ORDER_ID_PROMPT in content:
            return "withdrawal"
        return None
    return None


def _withdrawal_answer(
    order_id: str,
    withdrawal: WithdrawalRecord | None,
) -> str:
    if withdrawal is None:
        return f"未找到当前用户的提现订单 {order_id}。"
    return (
        f"Mock 查询结果：提现订单 {withdrawal.order_id}，"
        f"状态 {withdrawal.status}，数量 {withdrawal.size} {withdrawal.coin}，"
        f"网络 {withdrawal.chain}，更新时间 {withdrawal.updated_at}。"
    )


def _deposit_answer(
    txid: str,
    deposit: DepositRecord | None,
) -> str:
    if deposit is None:
        return (
            f"未找到当前用户的充值记录 {txid}。"
            "请确认 TxID、充值网络和到账账户是否正确。"
        )
    return (
        f"Mock 查询结果：充值 TxID {deposit.txid}，"
        f"状态 {deposit.status}，数量 {deposit.size} {deposit.coin}，"
        f"网络 {deposit.chain}，更新时间 {deposit.updated_at}。"
    )
