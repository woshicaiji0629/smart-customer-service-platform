"""Conversation application service."""

from __future__ import annotations

from uuid import UUID

from customer_service.business.service import (
    WithdrawalLookup,
    WithdrawalRecord,
    extract_withdrawal_order_id,
    is_withdrawal_tracking_query,
)
from customer_service.conversations.repository import (
    ConversationHistory,
    ConversationNotFoundError,
    ConversationRecord,
    ConversationRepository,
    ConversationTurn,
)
from customer_service.knowledge.rag import RagAnswer, RagHistoryMessage, RagService


MAX_MESSAGE_LENGTH = 4_000
MAX_HISTORY_MESSAGES = 6
WITHDRAWAL_ORDER_ID_PROMPT = (
    "请提供提现订单号，例如 WD-10001，我可以帮你查询处理状态。"
)


class ConversationService:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        rag_service: RagService | None,
        withdrawal_service: WithdrawalLookup,
    ) -> None:
        self._repository = repository
        self._rag_service = rag_service
        self._withdrawal_service = withdrawal_service

    async def create_conversation(self, user_id: str) -> ConversationRecord:
        return await self._repository.create_conversation(user_id)

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

        order_id = extract_withdrawal_order_id(normalized_content)
        if order_id is not None:
            withdrawal = self._withdrawal_service.get_withdrawal(user_id, order_id)
            answer = RagAnswer(
                answer=_withdrawal_answer(order_id, withdrawal),
                sources=[],
            )
        elif is_withdrawal_tracking_query(normalized_content):
            answer = RagAnswer(answer=WITHDRAWAL_ORDER_ID_PROMPT, sources=[])
        else:
            if self._rag_service is None:
                raise RagUnavailableError
            recent_messages = await self._repository.get_recent_messages(
                conversation_id,
                user_id=user_id,
                limit=MAX_HISTORY_MESSAGES,
            )
            history = [
                RagHistoryMessage(role=message.role, content=message.content)
                for message in recent_messages
            ]
            answer = await self._rag_service.answer(
                normalized_content,
                history=history,
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

    async def get_history(
        self,
        user_id: str,
        conversation_id: UUID,
    ) -> ConversationHistory:
        return await self._repository.get_history(conversation_id, user_id)


class RagUnavailableError(RuntimeError):
    """Raised when a conversation requires RAG but no model key is configured."""


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
