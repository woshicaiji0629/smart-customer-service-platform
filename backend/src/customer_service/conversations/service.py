"""Conversation application service."""

from __future__ import annotations

from uuid import UUID

from customer_service.conversations.repository import (
    ConversationHistory,
    ConversationNotFoundError,
    ConversationRecord,
    ConversationRepository,
    ConversationTurn,
)
from customer_service.knowledge.rag import RagService


MAX_MESSAGE_LENGTH = 4_000


class ConversationService:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        rag_service: RagService,
    ) -> None:
        self._repository = repository
        self._rag_service = rag_service

    async def create_conversation(self) -> ConversationRecord:
        return await self._repository.create_conversation()

    async def send_message(
        self,
        conversation_id: UUID,
        content: str,
    ) -> ConversationTurn:
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("content 不能为空")
        if len(normalized_content) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"content 不能超过 {MAX_MESSAGE_LENGTH} 个字符")
        if not await self._repository.conversation_exists(conversation_id):
            raise ConversationNotFoundError(str(conversation_id))

        answer = await self._rag_service.answer(normalized_content)
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
            user_content=normalized_content,
            assistant_content=answer.answer,
            assistant_sources=sources,
        )

    async def get_history(self, conversation_id: UUID) -> ConversationHistory:
        return await self._repository.get_history(conversation_id)
