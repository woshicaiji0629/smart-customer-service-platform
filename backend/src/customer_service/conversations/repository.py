"""PostgreSQL persistence for user-owned conversations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
    Text,
    and_,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


metadata = MetaData()
CONVERSATION_TITLE_LENGTH = 24

conversations = Table(
    "conversations",
    metadata,
    Column("id", PostgreSQLUUID(as_uuid=True), primary_key=True),
    Column("user_id", String(64), nullable=False, index=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

messages = Table(
    "messages",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "conversation_id",
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("role", String(16), nullable=False),
    Column("content", Text, nullable=False),
    Column("sources", JSONB, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint("role IN ('user', 'assistant')", name="messages_role_check"),
)


class ConversationNotFoundError(LookupError):
    """The requested conversation does not exist."""


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    conversation_id: UUID
    user_id: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MessageRecord:
    message_id: int
    conversation_id: UUID
    role: str
    content: str
    sources: list[dict[str, str]]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    user_message: MessageRecord
    assistant_message: MessageRecord


@dataclass(frozen=True, slots=True)
class ConversationHistory:
    conversation: ConversationRecord
    messages: list[MessageRecord]


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    conversation: ConversationRecord
    title: str


@dataclass(frozen=True, slots=True)
class ConversationCursor:
    updated_at: datetime
    conversation_id: UUID


@dataclass(frozen=True, slots=True)
class ConversationPage:
    items: list[ConversationSummary]
    next_cursor: ConversationCursor | None


class ConversationRepository:
    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(database_url)

    async def close(self) -> None:
        await self.engine.dispose()

    async def initialize_schema(self, *, reset: bool = False) -> None:
        async with self.engine.begin() as connection:
            if reset:
                await connection.run_sync(messages.drop, checkfirst=True)
                await connection.run_sync(conversations.drop, checkfirst=True)
            await connection.run_sync(metadata.create_all)

    async def create_conversation(self, user_id: str) -> ConversationRecord:
        conversation_id = uuid4()
        statement = (
            insert(conversations)
            .values(id=conversation_id, user_id=user_id)
            .returning(
                conversations.c.id,
                conversations.c.user_id,
                conversations.c.created_at,
                conversations.c.updated_at,
            )
        )
        async with self.engine.begin() as connection:
            row = (await connection.execute(statement)).mappings().one()
        return _conversation_from_row(row)

    async def conversation_exists(self, conversation_id: UUID, user_id: str) -> bool:
        statement = select(conversations.c.id).where(
            conversations.c.id == conversation_id,
            conversations.c.user_id == user_id,
        )
        async with self.engine.connect() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def list_conversations(
        self,
        user_id: str,
        *,
        limit: int,
        cursor: ConversationCursor | None,
    ) -> ConversationPage:
        if limit <= 0:
            raise ValueError("limit 必须大于 0")

        first_user_message = (
            select(messages.c.content)
            .where(
                messages.c.conversation_id == conversations.c.id,
                messages.c.role == "user",
            )
            .order_by(messages.c.created_at, messages.c.id)
            .limit(1)
            .correlate(conversations)
            .scalar_subquery()
        )
        statement = select(
            conversations,
            first_user_message.label("first_user_message"),
        ).where(conversations.c.user_id == user_id)
        if cursor is not None:
            statement = statement.where(
                or_(
                    conversations.c.updated_at < cursor.updated_at,
                    and_(
                        conversations.c.updated_at == cursor.updated_at,
                        conversations.c.id < cursor.conversation_id,
                    ),
                )
            )
        statement = (
            statement.order_by(
                conversations.c.updated_at.desc(),
                conversations.c.id.desc(),
            )
            .limit(limit + 1)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        page_rows = rows[:limit]
        items = [
            ConversationSummary(
                conversation=_conversation_from_row(row),
                title=_conversation_title(row["first_user_message"]),
            )
            for row in page_rows
        ]
        next_cursor = None
        if len(rows) > limit:
            last_conversation = items[-1].conversation
            next_cursor = ConversationCursor(
                updated_at=last_conversation.updated_at,
                conversation_id=last_conversation.conversation_id,
            )
        return ConversationPage(items=items, next_cursor=next_cursor)

    async def save_turn(
        self,
        *,
        conversation_id: UUID,
        user_id: str,
        user_content: str,
        assistant_content: str,
        assistant_sources: list[dict[str, str]],
    ) -> ConversationTurn:
        async with self.engine.begin() as connection:
            conversation_row = (
                await connection.execute(
                    update(conversations)
                    .where(
                        conversations.c.id == conversation_id,
                        conversations.c.user_id == user_id,
                    )
                    .values(updated_at=func.now())
                    .returning(conversations.c.id)
                )
            ).first()
            if conversation_row is None:
                raise ConversationNotFoundError(str(conversation_id))

            user_row = (
                await connection.execute(
                    insert(messages)
                    .values(
                        conversation_id=conversation_id,
                        role="user",
                        content=user_content,
                        sources=[],
                    )
                    .returning(*messages.c)
                )
            ).mappings().one()
            assistant_row = (
                await connection.execute(
                    insert(messages)
                    .values(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=assistant_content,
                        sources=assistant_sources,
                    )
                    .returning(*messages.c)
                )
            ).mappings().one()

        return ConversationTurn(
            user_message=_message_from_row(user_row),
            assistant_message=_message_from_row(assistant_row),
        )

    async def get_recent_messages(
        self,
        conversation_id: UUID,
        *,
        user_id: str,
        limit: int,
    ) -> list[MessageRecord]:
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        statement = (
            select(messages)
            .select_from(messages.join(conversations))
            .where(
                messages.c.conversation_id == conversation_id,
                conversations.c.user_id == user_id,
            )
            .order_by(messages.c.created_at.desc(), messages.c.id.desc())
            .limit(limit)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return list(reversed([_message_from_row(row) for row in rows]))

    async def get_history(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> ConversationHistory:
        conversation_statement = select(conversations).where(
            conversations.c.id == conversation_id,
            conversations.c.user_id == user_id,
        )
        message_statement = (
            select(messages)
            .where(messages.c.conversation_id == conversation_id)
            .order_by(messages.c.created_at, messages.c.id)
        )
        async with self.engine.connect() as connection:
            conversation_row = (
                await connection.execute(conversation_statement)
            ).mappings().one_or_none()
            if conversation_row is None:
                raise ConversationNotFoundError(str(conversation_id))
            message_rows = (await connection.execute(message_statement)).mappings().all()
        return ConversationHistory(
            conversation=_conversation_from_row(conversation_row),
            messages=[_message_from_row(row) for row in message_rows],
        )


def _conversation_from_row(row: Mapping[str, Any]) -> ConversationRecord:
    return ConversationRecord(
        conversation_id=row["id"],
        user_id=row["user_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _message_from_row(row: Mapping[str, Any]) -> MessageRecord:
    return MessageRecord(
        message_id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        content=row["content"],
        sources=[dict(source) for source in row["sources"]],
        created_at=row["created_at"],
    )


def _conversation_title(first_user_message: str | None) -> str:
    if first_user_message is None:
        return "空白会话"
    normalized = " ".join(first_user_message.split())
    if len(normalized) > CONVERSATION_TITLE_LENGTH:
        return f"{normalized[:CONVERSATION_TITLE_LENGTH]}…"
    return normalized
