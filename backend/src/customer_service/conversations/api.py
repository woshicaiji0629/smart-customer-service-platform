"""HTTP API for authenticated user conversations."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from customer_service.auth.api import CurrentUserDependency
from customer_service.conversations.repository import (
    ConversationCursor,
    ConversationHistory,
    ConversationNotFoundError,
    ConversationRecord,
    ConversationSummary,
    ConversationTurn,
    MessageRecord,
)
from customer_service.conversations.service import (
    DEFAULT_CONVERSATION_LIST_LIMIT,
    MAX_MESSAGE_LENGTH,
    MAX_CONVERSATION_LIST_LIMIT,
    ConversationService,
    RagUnavailableError,
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


class NextActionResponse(BaseModel):
    type: str
    expected_input: str
    manual_fallback_candidate: bool


class MessageResponse(BaseModel):
    id: int
    conversation_id: UUID
    role: str
    content: str
    sources: list[SourceSnapshotResponse]
    created_at: datetime
    next_action: NextActionResponse | None = None


class ConversationResponse(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime


class ConversationTurnResponse(BaseModel):
    user_message: MessageResponse
    assistant_message: MessageResponse


class ConversationSummaryResponse(ConversationResponse):
    title: str


class ConversationListResponse(BaseModel):
    items: list[ConversationSummaryResponse]
    next_cursor: str | None


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
    user: CurrentUserDependency,
) -> ConversationResponse:
    try:
        conversation = await service.create_conversation(user.user_id)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="会话数据库暂时不可用",
        ) from exc
    return _conversation_response(conversation)


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    service: ConversationServiceDependency,
    user: CurrentUserDependency,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_CONVERSATION_LIST_LIMIT),
    ] = DEFAULT_CONVERSATION_LIST_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> ConversationListResponse:
    decoded_cursor = _decode_cursor(cursor) if cursor is not None else None
    try:
        page = await service.list_conversations(
            user.user_id,
            limit=limit,
            cursor=decoded_cursor,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="会话数据库暂时不可用",
        ) from exc
    return ConversationListResponse(
        items=[_summary_response(summary) for summary in page.items],
        next_cursor=(
            _encode_cursor(page.next_cursor) if page.next_cursor is not None else None
        ),
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationTurnResponse,
)
async def send_message(
    conversation_id: UUID,
    body: SendMessageRequest,
    service: ConversationServiceDependency,
    user: CurrentUserDependency,
) -> ConversationTurnResponse:
    try:
        turn = await service.send_message(
            user.user_id,
            conversation_id,
            body.content,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    except RagUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG 问答服务未配置",
        ) from exc
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
    user: CurrentUserDependency,
) -> ConversationHistoryResponse:
    try:
        history = await service.get_history(user.user_id, conversation_id)
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


def _message_response(
    record: MessageRecord,
    *,
    next_action: dict[str, object] | None = None,
) -> MessageResponse:
    return MessageResponse(
        id=record.message_id,
        conversation_id=record.conversation_id,
        role=record.role,
        content=record.content,
        sources=[SourceSnapshotResponse(**source) for source in record.sources],
        created_at=record.created_at,
        next_action=(
            NextActionResponse(**next_action) if next_action is not None else None
        ),
    )


def _summary_response(summary: ConversationSummary) -> ConversationSummaryResponse:
    conversation = summary.conversation
    return ConversationSummaryResponse(
        id=conversation.conversation_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        title=summary.title,
    )


def _encode_cursor(cursor: ConversationCursor) -> str:
    payload = json.dumps(
        {
            "updated_at": cursor.updated_at.isoformat(),
            "id": str(cursor.conversation_id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> ConversationCursor:
    try:
        padding = "=" * (-len(value) % 4)
        payload = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
        fields = json.loads(payload.decode("utf-8"))
        updated_at = datetime.fromisoformat(fields["updated_at"])
        conversation_id = UUID(fields["id"])
        if updated_at.utcoffset() is None:
            raise ValueError("updated_at 必须包含时区")
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="cursor 无效",
        ) from exc
    return ConversationCursor(
        updated_at=updated_at,
        conversation_id=conversation_id,
    )


def _turn_response(turn: ConversationTurn) -> ConversationTurnResponse:
    return ConversationTurnResponse(
        user_message=_message_response(turn.user_message),
        assistant_message=_message_response(
            turn.assistant_message,
            next_action=turn.next_action,
        ),
    )


def _history_response(history: ConversationHistory) -> ConversationHistoryResponse:
    conversation = history.conversation
    return ConversationHistoryResponse(
        id=conversation.conversation_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[_message_response(message) for message in history.messages],
    )
