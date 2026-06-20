import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from customer_service.auth.api import get_current_user
from customer_service.auth.session import AuthenticatedUser
from customer_service.business.service import MOCK_WITHDRAWAL_SERVICE
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
from customer_service.conversations.service import (
    ConversationService,
    RagUnavailableError,
)
from customer_service.knowledge.rag import RagAnswer, RagHistoryMessage, RagSource
from customer_service.main import app


CONVERSATION_ID = UUID("11111111-1111-1111-1111-111111111111")
CREATED_AT = datetime(2026, 6, 19, 8, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 6, 19, 8, 1, tzinfo=UTC)
USER_ID = "10001"
OTHER_USER_ID = "10002"
CURRENT_USER = AuthenticatedUser(USER_ID, "模拟用户 Alice")


def _conversation() -> ConversationRecord:
    return ConversationRecord(
        conversation_id=CONVERSATION_ID,
        user_id=USER_ID,
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
        self.checked_user_id: str | None = None

    async def create_conversation(self, user_id: str) -> ConversationRecord:
        self.checked_user_id = user_id
        return _conversation()

    async def conversation_exists(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> bool:
        self.checked_user_id = user_id
        return self.exists and user_id == USER_ID

    async def save_turn(self, **values: object) -> ConversationTurn:
        self.saved = values
        return _turn()

    async def get_recent_messages(
        self,
        conversation_id: UUID,
        *,
        user_id: str,
        limit: int,
    ) -> list[MessageRecord]:
        self.checked_user_id = user_id
        self.recent_limit = limit
        return self.recent_messages

    async def get_history(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> ConversationHistory:
        self.checked_user_id = user_id
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
    assert set(conversations.c.keys()) == {
        "id",
        "user_id",
        "created_at",
        "updated_at",
    }
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
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
    )

    turn = asyncio.run(
        service.send_message(USER_ID, CONVERSATION_ID, "  提现手续费是多少  ")
    )

    assert rag_service.question == "提现手续费是多少"
    assert rag_service.history == []
    assert repository.recent_limit == 6
    assert repository.checked_user_id == USER_ID
    assert repository.saved == {
        "conversation_id": CONVERSATION_ID,
        "user_id": USER_ID,
        "user_content": "提现手续费是多少",
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
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
    )

    try:
        asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "提现没有到账"))
    except ConversationNotFoundError:
        pass
    else:
        raise AssertionError("expected ConversationNotFoundError")

    assert rag_service.question is None
    assert repository.saved is None


def test_conversation_service_rejects_another_users_conversation() -> None:
    repository = FakeConversationRepository()
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=rag_service,  # type: ignore[arg-type]
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
    )

    try:
        asyncio.run(
            service.send_message(OTHER_USER_ID, CONVERSATION_ID, "提现没有到账")
        )
    except ConversationNotFoundError:
        pass
    else:
        raise AssertionError("expected ConversationNotFoundError")

    assert repository.checked_user_id == OTHER_USER_ID
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
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "那我要联系谁？"))

    assert repository.recent_limit == 6
    assert rag_service.history == [
        RagHistoryMessage(role="user", content="提现没有到账"),
        RagHistoryMessage(role="assistant", content="请查询 TxID。[资料 1]"),
    ]


def test_conversation_service_routes_order_id_to_business_query() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "查询 wd-10001"))

    assert repository.recent_limit is None
    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "Mock 查询结果：提现订单 WD-10001，状态 success，数量 120.00 USDT，"
        "网络 TRC20，更新时间 2026-06-20T10:30:00+08:00。"
    )
    assert repository.saved["assistant_sources"] == []


def test_conversation_service_requests_order_id_for_tracking_query() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
    )

    asyncio.run(
        service.send_message(
            USER_ID,
            CONVERSATION_ID,
            "提现完成但钱包没到账怎么办？",
        )
    )

    assert repository.recent_limit is None
    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "请提供提现订单号，例如 WD-10001，我可以帮你查询处理状态。"
    )
    assert repository.saved["assistant_sources"] == []


def test_conversation_service_rejects_rag_question_when_unconfigured() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
    )

    try:
        asyncio.run(
            service.send_message(USER_ID, CONVERSATION_ID, "提现手续费是多少？")
        )
    except RagUnavailableError:
        pass
    else:
        raise AssertionError("expected RagUnavailableError")

    assert repository.recent_limit is None
    assert repository.saved is None


def test_conversation_service_does_not_expose_another_users_order() -> None:
    repository = FakeConversationRepository()
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=rag_service,  # type: ignore[arg-type]
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "查询 WD-10002"))

    assert rag_service.question is None
    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "未找到当前用户的提现订单 WD-10002。"
    )


class FakeConversationService:
    async def create_conversation(self, user_id: str) -> ConversationRecord:
        assert user_id == USER_ID
        return _conversation()

    async def send_message(
        self,
        user_id: str,
        conversation_id: UUID,
        content: str,
    ) -> ConversationTurn:
        assert user_id == USER_ID
        assert conversation_id == CONVERSATION_ID
        assert content == "提现没有到账"
        return _turn()

    async def get_history(
        self,
        user_id: str,
        conversation_id: UUID,
    ) -> ConversationHistory:
        assert user_id == USER_ID
        assert conversation_id == CONVERSATION_ID
        turn = _turn()
        return ConversationHistory(
            conversation=_conversation(),
            messages=[turn.user_message, turn.assistant_message],
        )


def test_conversation_api_create_send_and_get_history() -> None:
    app.dependency_overrides[get_conversation_service] = FakeConversationService
    app.dependency_overrides[get_current_user] = lambda: CURRENT_USER
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
        async def get_history(
            self,
            user_id: str,
            conversation_id: UUID,
        ) -> ConversationHistory:
            raise ConversationNotFoundError(str(conversation_id))

    app.dependency_overrides[get_conversation_service] = MissingConversationService
    app.dependency_overrides[get_current_user] = lambda: CURRENT_USER
    try:
        response = TestClient(app).get(f"/conversations/{CONVERSATION_ID}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "会话不存在"}


def test_conversation_api_returns_503_when_rag_is_unconfigured() -> None:
    class UnconfiguredRagConversationService(FakeConversationService):
        async def send_message(
            self,
            user_id: str,
            conversation_id: UUID,
            content: str,
        ) -> ConversationTurn:
            raise RagUnavailableError

    app.dependency_overrides[get_conversation_service] = (
        UnconfiguredRagConversationService
    )
    app.dependency_overrides[get_current_user] = lambda: CURRENT_USER
    try:
        response = TestClient(app).post(
            f"/conversations/{CONVERSATION_ID}/messages",
            json={"content": "提现手续费是多少？"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "RAG 问答服务未配置"}
