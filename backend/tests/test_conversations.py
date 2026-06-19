import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from customer_service.conversations.api import get_conversation_service
from customer_service.conversations.repository import (
    ConversationHistory,
    ConversationNotFoundError,
    ConversationRecord,
    ConversationTurn,
    MessageRecord,
    conversations,
    messages,
)
from customer_service.conversations.service import ConversationService
from customer_service.knowledge.rag import RagAnswer, RagHistoryMessage, RagSource
from customer_service.main import app


CONVERSATION_ID = UUID("11111111-1111-1111-1111-111111111111")
CREATED_AT = datetime(2026, 6, 19, 8, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 6, 19, 8, 1, tzinfo=UTC)


def _conversation() -> ConversationRecord:
    return ConversationRecord(
        conversation_id=CONVERSATION_ID,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )


def _turn() -> ConversationTurn:
    return ConversationTurn(
        user_message=MessageRecord(
            message_id=1,
            conversation_id=CONVERSATION_ID,
            role="user",
            content="提现没有到账",
            sources=[],
            created_at=CREATED_AT,
        ),
        assistant_message=MessageRecord(
            message_id=2,
            conversation_id=CONVERSATION_ID,
            role="assistant",
            content="请查询 TxID。[资料 1]",
            sources=[
                {
                    "article_id": "article-1",
                    "title": "提现没有到账",
                    "source_url": "https://example.com/article-1",
                }
            ],
            created_at=UPDATED_AT,
        ),
    )


class FakeConversationRepository:
    def __init__(
        self,
        *,
        exists: bool = True,
        recent_messages: list[MessageRecord] | None = None,
    ) -> None:
        self.exists = exists
        self.recent_messages = recent_messages or []
        self.recent_limit: int | None = None
        self.saved: dict[str, object] | None = None

    async def create_conversation(self) -> ConversationRecord:
        return _conversation()

    async def conversation_exists(self, conversation_id: UUID) -> bool:
        return self.exists

    async def save_turn(self, **values: object) -> ConversationTurn:
        self.saved = values
        return _turn()

    async def get_recent_messages(
        self,
        conversation_id: UUID,
        *,
        limit: int,
    ) -> list[MessageRecord]:
        self.recent_limit = limit
        return self.recent_messages

    async def get_history(self, conversation_id: UUID) -> ConversationHistory:
        if not self.exists:
            raise ConversationNotFoundError(str(conversation_id))
        turn = _turn()
        return ConversationHistory(
            conversation=_conversation(),
            messages=[turn.user_message, turn.assistant_message],
        )


class FakeRagService:
    def __init__(self) -> None:
        self.question: str | None = None
        self.history: list[RagHistoryMessage] = []

    async def answer(
        self,
        question: str,
        *,
        history: list[RagHistoryMessage],
    ) -> RagAnswer:
        self.question = question
        self.history = history
        return RagAnswer(
            answer="请查询 TxID。[资料 1]",
            sources=[
                RagSource(
                    article_id="article-1",
                    title="提现没有到账",
                    source_url="https://example.com/article-1",
                )
            ],
        )


def test_conversation_schema_has_expected_columns() -> None:
    assert set(conversations.c.keys()) == {"id", "created_at", "updated_at"}
    assert set(messages.c.keys()) == {
        "id",
        "conversation_id",
        "role",
        "content",
        "sources",
        "created_at",
    }


def test_conversation_service_saves_complete_turn_after_rag() -> None:
    repository = FakeConversationRepository()
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=rag_service,  # type: ignore[arg-type]
    )

    turn = asyncio.run(service.send_message(CONVERSATION_ID, "  提现没有到账  "))

    assert rag_service.question == "提现没有到账"
    assert rag_service.history == []
    assert repository.recent_limit == 6
    assert repository.saved == {
        "conversation_id": CONVERSATION_ID,
        "user_content": "提现没有到账",
        "assistant_content": "请查询 TxID。[资料 1]",
        "assistant_sources": [
            {
                "article_id": "article-1",
                "title": "提现没有到账",
                "source_url": "https://example.com/article-1",
            }
        ],
    }
    assert turn.assistant_message.role == "assistant"


def test_conversation_service_rejects_missing_conversation_before_rag() -> None:
    repository = FakeConversationRepository(exists=False)
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=rag_service,  # type: ignore[arg-type]
    )

    try:
        asyncio.run(service.send_message(CONVERSATION_ID, "提现没有到账"))
    except ConversationNotFoundError:
        pass
    else:
        raise AssertionError("expected ConversationNotFoundError")

    assert rag_service.question is None
    assert repository.saved is None


def test_conversation_service_passes_recent_messages_to_rag() -> None:
    previous_turn = _turn()
    repository = FakeConversationRepository(
        recent_messages=[
            previous_turn.user_message,
            previous_turn.assistant_message,
        ]
    )
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=rag_service,  # type: ignore[arg-type]
    )

    asyncio.run(service.send_message(CONVERSATION_ID, "那我要联系谁？"))

    assert repository.recent_limit == 6
    assert rag_service.history == [
        RagHistoryMessage(role="user", content="提现没有到账"),
        RagHistoryMessage(role="assistant", content="请查询 TxID。[资料 1]"),
    ]


class FakeConversationService:
    async def create_conversation(self) -> ConversationRecord:
        return _conversation()

    async def send_message(
        self,
        conversation_id: UUID,
        content: str,
    ) -> ConversationTurn:
        assert conversation_id == CONVERSATION_ID
        assert content == "提现没有到账"
        return _turn()

    async def get_history(self, conversation_id: UUID) -> ConversationHistory:
        assert conversation_id == CONVERSATION_ID
        turn = _turn()
        return ConversationHistory(
            conversation=_conversation(),
            messages=[turn.user_message, turn.assistant_message],
        )


def test_conversation_api_create_send_and_get_history() -> None:
    app.dependency_overrides[get_conversation_service] = FakeConversationService
    client = TestClient(app)
    try:
        create_response = client.post("/conversations")
        send_response = client.post(
            f"/conversations/{CONVERSATION_ID}/messages",
            json={"content": "提现没有到账"},
        )
        history_response = client.get(f"/conversations/{CONVERSATION_ID}")
    finally:
        app.dependency_overrides.clear()

    assert create_response.status_code == 201
    assert create_response.json()["id"] == str(CONVERSATION_ID)
    assert send_response.status_code == 200
    assert send_response.json()["assistant_message"]["sources"][0]["article_id"] == (
        "article-1"
    )
    assert history_response.status_code == 200
    assert [message["role"] for message in history_response.json()["messages"]] == [
        "user",
        "assistant",
    ]


def test_conversation_api_returns_404_for_missing_conversation() -> None:
    class MissingConversationService(FakeConversationService):
        async def get_history(self, conversation_id: UUID) -> ConversationHistory:
            raise ConversationNotFoundError(str(conversation_id))

    app.dependency_overrides[get_conversation_service] = MissingConversationService
    try:
        response = TestClient(app).get(f"/conversations/{CONVERSATION_ID}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "会话不存在"}
