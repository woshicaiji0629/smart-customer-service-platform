"""HTTP API for anonymous conversations."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from customer_service.conversations.repository import (
    ConversationHistory,
    ConversationNotFoundError,
    ConversationRecord,
    ConversationTurn,
    MessageRecord,
)
from customer_service.conversations.service import (
    MAX_MESSAGE_LENGTH,
    ConversationService,
)
from customer_service.knowledge.chat import ChatCompletionError
from customer_service.knowledge.embeddings import EmbeddingError
from customer_service.knowledge.rag import RagCitationError


router = APIRouter(prefix="/conversations", tags=["conversations"])


class SendMessageRequest(BaseModel):
    content: str = Field(max_length=MAX_MESSAGE_LENGTH)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content 不能为空")
        return value


class SourceSnapshotResponse(BaseModel):
    article_id: str
    title: str
    source_url: str


class MessageResponse(BaseModel):
    id: int
    conversation_id: UUID
    role: str
    content: str
    sources: list[SourceSnapshotResponse]
    created_at: datetime


class ConversationResponse(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime


class ConversationTurnResponse(BaseModel):
    user_message: MessageResponse
    assistant_message: MessageResponse


class ConversationHistoryResponse(ConversationResponse):
    messages: list[MessageResponse]


def get_conversation_service(request: Request) -> ConversationService:
    service: ConversationService | None = getattr(
        request.app.state,
        "conversation_service",
        None,
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="会话服务未配置",
        )
    return service


ConversationServiceDependency = Annotated[
    ConversationService,
    Depends(get_conversation_service),
]


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    service: ConversationServiceDependency,
) -> ConversationResponse:
    try:
        conversation = await service.create_conversation()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="会话数据库暂时不可用",
        ) from exc
    return _conversation_response(conversation)


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationTurnResponse,
)
async def send_message(
    conversation_id: UUID,
    body: SendMessageRequest,
    service: ConversationServiceDependency,
) -> ConversationTurnResponse:
    try:
        turn = await service.send_message(conversation_id, body.content)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    except EmbeddingError as exc:
        raise HTTPException(status_code=502, detail="Embedding 服务请求失败") from exc
    except ChatCompletionError as exc:
        raise HTTPException(status_code=502, detail="大模型服务请求失败") from exc
    except RagCitationError as exc:
        raise HTTPException(status_code=502, detail="大模型引用校验失败") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="会话数据库暂时不可用",
        ) from exc
    return _turn_response(turn)


@router.get("/{conversation_id}", response_model=ConversationHistoryResponse)
async def get_conversation(
    conversation_id: UUID,
    service: ConversationServiceDependency,
) -> ConversationHistoryResponse:
    try:
        history = await service.get_history(conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="会话数据库暂时不可用",
        ) from exc
    return _history_response(history)


def _conversation_response(record: ConversationRecord) -> ConversationResponse:
    return ConversationResponse(
        id=record.conversation_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _message_response(record: MessageRecord) -> MessageResponse:
    return MessageResponse(
        id=record.message_id,
        conversation_id=record.conversation_id,
        role=record.role,
        content=record.content,
        sources=[SourceSnapshotResponse(**source) for source in record.sources],
        created_at=record.created_at,
    )


def _turn_response(turn: ConversationTurn) -> ConversationTurnResponse:
    return ConversationTurnResponse(
        user_message=_message_response(turn.user_message),
        assistant_message=_message_response(turn.assistant_message),
    )


def _history_response(history: ConversationHistory) -> ConversationHistoryResponse:
    conversation = history.conversation
    return ConversationHistoryResponse(
        id=conversation.conversation_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[_message_response(message) for message in history.messages],
    )
