import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from customer_service.auth.api import get_current_user
from customer_service.auth.session import AuthenticatedUser
from customer_service.business.service import MOCK_WITHDRAWAL_SERVICE
from customer_service.conversations.api import (
    _decode_cursor,
    _encode_cursor,
    get_conversation_service,
)
from customer_service.conversations.repository import (
    ConversationCursor,
    ConversationHistory,
    ConversationNotFoundError,
    ConversationPage,
    ConversationRecord,
    ConversationRepository,
    ConversationSummary,
    ConversationTurn,
    MessageRecord,
    _conversation_title,
    conversation_turn_traces,
    conversations,
    messages,
)
from customer_service.conversations.service import (
    ConversationService,
    RagUnavailableError,
)
from customer_service.knowledge.rag import RagAnswer, RagHistoryMessage, RagSource
from customer_service.intents.service import (
    IntentDecision,
    IntentHistoryMessage,
    IntentService,
)
from customer_service.main import app


CONVERSATION_ID = UUID("11111111-1111-1111-1111-111111111111")
CREATED_AT = datetime(2026, 6, 19, 8, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 6, 19, 8, 1, tzinfo=UTC)
USER_ID = "10001"
OTHER_USER_ID = "10002"
CURRENT_USER = AuthenticatedUser(USER_ID, "模拟用户 Alice")
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


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


def _message(
    *,
    message_id: int,
    role: str,
    content: str,
    created_at: datetime = UPDATED_AT,
) -> MessageRecord:
    return MessageRecord(
        message_id=message_id,
        conversation_id=CONVERSATION_ID,
        role=role,
        content=content,
        sources=[],
        created_at=created_at,
    )


class FakeConversationRepository:
    def __init__(
        self,
        *,
        exists: bool = True,
        recent_messages: list[MessageRecord] | None = None,
        trace_error: bool = False,
    ) -> None:
        self.exists = exists
        self.recent_messages = recent_messages or []
        self.trace_error = trace_error
        self.recent_limit: int | None = None
        self.saved: dict[str, object] | None = None
        self.traces: list[dict[str, object]] = []
        self.checked_user_id: str | None = None
        self.list_limit: int | None = None
        self.list_cursor: ConversationCursor | None = None

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

    async def list_conversations(
        self,
        user_id: str,
        *,
        limit: int,
        cursor: ConversationCursor | None,
    ) -> ConversationPage:
        self.checked_user_id = user_id
        self.list_limit = limit
        self.list_cursor = cursor
        return ConversationPage(
            items=[
                ConversationSummary(
                    conversation=_conversation(),
                    title="提现没有到账",
                )
            ],
            next_cursor=None,
        )

    async def save_turn(self, **values: object) -> ConversationTurn:
        self.saved = values
        return _turn()

    async def record_turn_trace(self, **values: object) -> None:
        if self.trace_error:
            raise SQLAlchemyError("trace database unavailable")
        self.traces.append(values)

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


class FakeIntentRecognizer:
    def __init__(self) -> None:
        self.history: list[IntentHistoryMessage] = []

    async def recognize(
        self,
        content: str,
        *,
        history: list[IntentHistoryMessage],
    ) -> IntentDecision:
        self.history = history
        return IntentDecision(
            route="knowledge_rag",
            topic="other",
            confidence=1.0,
            entities={},
            missing_fields=(),
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
    assert set(conversation_turn_traces.c.keys()) == {
        "id",
        "conversation_id",
        "user_id",
        "user_message_id",
        "assistant_message_id",
        "route",
        "topic",
        "confidence",
        "entities",
        "missing_fields",
        "handling_result",
        "is_inactive_reset",
        "created_at",
    }


def test_conversation_service_saves_complete_turn_after_rag() -> None:
    repository = FakeConversationRepository()
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=rag_service,  # type: ignore[arg-type]
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=FakeIntentRecognizer(),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
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
    assert repository.traces == [
        {
            "conversation_id": CONVERSATION_ID,
            "user_id": USER_ID,
            "user_message_id": 1,
            "assistant_message_id": 2,
            "route": "knowledge_rag",
            "topic": "other",
            "confidence": 1.0,
            "entities": {},
            "missing_fields": (),
            "handling_result": "rag_answer",
            "is_inactive_reset": False,
        }
    ]
    assert turn.assistant_message.role == "assistant"


def test_conversation_service_lists_current_users_conversations() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=FakeIntentRecognizer(),
    )

    cursor = ConversationCursor(
        updated_at=UPDATED_AT,
        conversation_id=CONVERSATION_ID,
    )
    page = asyncio.run(
        service.list_conversations(USER_ID, limit=20, cursor=cursor)
    )

    assert repository.checked_user_id == USER_ID
    assert repository.list_limit == 20
    assert repository.list_cursor == cursor
    assert page.items[0].title == "提现没有到账"
    assert page.next_cursor is None


def test_conversation_title_uses_first_question_and_truncates() -> None:
    assert _conversation_title(None) == "空白会话"
    assert _conversation_title("  提现完成\n但钱包没到账  ") == "提现完成 但钱包没到账"
    assert _conversation_title("问" * 25) == f"{'问' * 24}…"


def test_conversation_cursor_round_trip() -> None:
    cursor = ConversationCursor(
        updated_at=UPDATED_AT,
        conversation_id=CONVERSATION_ID,
    )

    assert _decode_cursor(_encode_cursor(cursor)) == cursor


@pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="需要通过 TEST_DATABASE_URL 显式启用 PostgreSQL 集成测试",
)
def test_list_conversations_against_postgresql() -> None:
    asyncio.run(_test_list_conversations_against_postgresql())


async def _test_list_conversations_against_postgresql() -> None:
    assert TEST_DATABASE_URL is not None
    schema_name = f"test_conversations_{uuid4().hex}"
    admin_engine = create_async_engine(TEST_DATABASE_URL)
    repository = None
    schema_created = False

    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        schema_created = True

        repository = ConversationRepository(TEST_DATABASE_URL)
        await repository.engine.dispose()
        repository.engine = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        await repository.initialize_schema()

        older = await repository.create_conversation(USER_ID)
        newer = await repository.create_conversation(USER_ID)
        other_user = await repository.create_conversation(OTHER_USER_ID)
        await repository.save_turn(
            conversation_id=older.conversation_id,
            user_id=USER_ID,
            user_content=f"  {'问' * 25}\n",
            assistant_content="回答",
            assistant_sources=[],
        )
        await repository.save_turn(
            conversation_id=other_user.conversation_id,
            user_id=OTHER_USER_ID,
            user_content="其他用户的问题",
            assistant_content="回答",
            assistant_sources=[],
        )

        async with repository.engine.begin() as connection:
            shared_updated_at = datetime(2026, 6, 19, 8, 0, tzinfo=UTC)
            await connection.execute(
                update(conversations)
                .where(conversations.c.id == older.conversation_id)
                .values(updated_at=shared_updated_at)
            )
            await connection.execute(
                update(conversations)
                .where(conversations.c.id == newer.conversation_id)
                .values(updated_at=shared_updated_at)
            )

        first_page = await repository.list_conversations(
            USER_ID,
            limit=1,
            cursor=None,
        )
        assert first_page.next_cursor is not None
        second_page = await repository.list_conversations(
            USER_ID,
            limit=1,
            cursor=first_page.next_cursor,
        )
        other_users_page = await repository.list_conversations(
            OTHER_USER_ID,
            limit=50,
            cursor=None,
        )

        expected_ids = sorted(
            [older.conversation_id, newer.conversation_id],
            reverse=True,
        )
        assert [item.conversation.conversation_id for item in first_page.items] == [
            expected_ids[0]
        ]
        assert first_page.next_cursor == ConversationCursor(
            updated_at=shared_updated_at,
            conversation_id=expected_ids[0],
        )
        assert [item.conversation.conversation_id for item in second_page.items] == [
            expected_ids[1]
        ]
        assert second_page.next_cursor is None
        titles_by_id = {
            item.conversation.conversation_id: item.title
            for item in [*first_page.items, *second_page.items]
        }
        assert titles_by_id[older.conversation_id] == f"{'问' * 24}…"
        assert titles_by_id[newer.conversation_id] == "空白会话"
        assert [item.conversation.user_id for item in other_users_page.items] == [
            OTHER_USER_ID
        ]
        assert other_users_page.next_cursor is None
    finally:
        try:
            if repository is not None:
                await repository.close()
            if schema_created:
                async with admin_engine.begin() as connection:
                    await connection.execute(
                        text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                    )
        finally:
            await admin_engine.dispose()


def test_conversation_service_rejects_missing_conversation_before_rag() -> None:
    repository = FakeConversationRepository(exists=False)
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=rag_service,  # type: ignore[arg-type]
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=FakeIntentRecognizer(),
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
        intent_service=FakeIntentRecognizer(),
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
        intent_service=FakeIntentRecognizer(),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "那我要联系谁？"))

    assert repository.recent_limit == 6
    assert rag_service.history == [
        RagHistoryMessage(role="user", content="提现没有到账"),
        RagHistoryMessage(role="assistant", content="请查询 TxID。[资料 1]"),
    ]


def test_conversation_service_closes_inactive_context_before_rag() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            _message(
                message_id=1,
                role="user",
                content="提现没有到账",
                created_at=CREATED_AT,
            ),
            _message(
                message_id=2,
                role="assistant",
                content="请查询 TxID。[资料 1]",
                created_at=UPDATED_AT,
            ),
        ]
    )
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=rag_service,  # type: ignore[arg-type]
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=FakeIntentRecognizer(),
        now=lambda: datetime(2026, 6, 19, 8, 7, tzinfo=UTC),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "那我要联系谁？"))

    assert rag_service.history == []
    assert repository.saved is not None
    assert str(repository.saved["assistant_content"]).startswith(
        "由于你超过 5 分钟未回复，之前的问题已自动关闭。"
    )
    assert repository.traces[0]["handling_result"] == "rag_answer"
    assert repository.traces[0]["is_inactive_reset"] is True


def test_conversation_service_routes_order_id_to_business_query() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "查询 wd-10001"))

    assert repository.recent_limit == 6
    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "Mock 查询结果：提现订单 WD-10001，状态 success，数量 120.00 USDT，"
        "网络 TRC20，更新时间 2026-06-20T10:30:00+08:00。"
    )
    assert repository.saved["assistant_sources"] == []
    assert repository.traces[0]["route"] == "business_query"
    assert repository.traces[0]["topic"] == "withdrawal"
    assert repository.traces[0]["entities"] == {"order_id": "WD-10001"}
    assert repository.traces[0]["handling_result"] == "business_withdrawal_found"


def test_conversation_service_requests_order_id_for_tracking_query() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    asyncio.run(
        service.send_message(
            USER_ID,
            CONVERSATION_ID,
            "提现完成但钱包没到账怎么办？",
        )
    )

    assert repository.recent_limit == 6
    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "请提供提现订单号，例如 WD-10001，我可以帮你查询处理状态。"
    )
    assert repository.saved["assistant_sources"] == []
    assert repository.traces[0]["handling_result"] == "missing_withdrawal_order_id"
    assert repository.traces[0]["missing_fields"] == ("order_id",)


def test_conversation_service_requests_txid_for_deposit_query() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "我的充值一直没到账"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "请提供充值 TxID，例如 TX-10001，我可以帮你查询充值处理状态。"
    )
    assert repository.saved["assistant_sources"] == []
    assert repository.traces[0]["topic"] == "deposit"
    assert repository.traces[0]["handling_result"] == "missing_deposit_txid"


def test_conversation_service_routes_deposit_txid_to_business_query() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "帮我查 tx-10001"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "Mock 查询结果：充值 TxID TX-10001，状态 success，数量 88.00 USDT，"
        "网络 TRC20，更新时间 2026-06-20T12:30:00+08:00。"
    )
    assert repository.saved["assistant_sources"] == []
    assert repository.traces[0]["handling_result"] == "business_deposit_found"


def test_conversation_service_does_not_expose_another_users_deposit() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "帮我查 TX-10002"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "未找到当前用户的充值记录 TX-10002。"
        "请确认 TxID、充值网络和到账账户是否正确。"
    )
    assert repository.saved["assistant_sources"] == []
    assert repository.traces[0]["handling_result"] == "business_deposit_not_found"


def test_conversation_service_keeps_pending_deposit_context() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            MessageRecord(
                message_id=1,
                conversation_id=CONVERSATION_ID,
                role="assistant",
                content="请提供充值 TxID，例如 TX-10001，我可以帮你查询充值处理状态。",
                sources=[],
                created_at=CREATED_AT,
            )
        ]
    )
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "还没找到"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "请提供充值 TxID，例如 TX-10001，我可以帮你查询充值处理状态。"
    )


def test_conversation_service_drops_pending_context_after_inactivity() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            _message(
                message_id=1,
                role="assistant",
                content="请提供充值 TxID，例如 TX-10001，我可以帮你查询充值处理状态。",
                created_at=UPDATED_AT,
            )
        ]
    )
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 7, tzinfo=UTC),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "还没找到"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "由于你超过 5 分钟未回复，之前的问题已自动关闭。"
        "我们将按新的问题重新处理。\n\n"
        "请补充说明你遇到的具体问题、操作步骤或页面提示。"
    )


def test_conversation_service_keeps_pending_withdrawal_context() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            MessageRecord(
                message_id=1,
                conversation_id=CONVERSATION_ID,
                role="assistant",
                content="请提供提现订单号，例如 WD-10001，我可以帮你查询处理状态。",
                sources=[],
                created_at=CREATED_AT,
            )
        ]
    )
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "还没找到"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "请提供提现订单号，例如 WD-10001，我可以帮你查询处理状态。"
    )


def test_conversation_service_does_not_override_explicit_intent_with_pending_context() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            MessageRecord(
                message_id=1,
                conversation_id=CONVERSATION_ID,
                role="assistant",
                content="请提供充值 TxID，例如 TX-10001，我可以帮你查询充值处理状态。",
                sources=[],
                created_at=CREATED_AT,
            )
        ]
    )
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "查询 WD-10001"))

    assert repository.saved is not None
    assert "提现订单 WD-10001" in str(repository.saved["assistant_content"])


def test_conversation_service_uses_intent_rules_in_production_path() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "查询 WD-10001"))

    assert repository.saved is not None
    assert "状态 success" in str(repository.saved["assistant_content"])


def test_conversation_service_clarifies_unknown_intent_without_model() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "这个怎么弄"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "请补充说明你遇到的具体问题、操作步骤或页面提示。"
    )
    assert repository.traces[0]["route"] == "unknown"
    assert repository.traces[0]["handling_result"] == "unknown"


def test_conversation_service_keeps_response_when_trace_write_fails() -> None:
    repository = FakeConversationRepository(trace_error=True)
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    turn = asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "查询 WD-10001"))

    assert turn.assistant_message.role == "assistant"
    assert repository.saved is not None
    assert "提现订单 WD-10001" in str(repository.saved["assistant_content"])
    assert repository.traces == []


def test_conversation_service_rejects_rag_question_when_unconfigured() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=FakeIntentRecognizer(),
    )

    try:
        asyncio.run(
            service.send_message(USER_ID, CONVERSATION_ID, "提现手续费是多少？")
        )
    except RagUnavailableError:
        pass
    else:
        raise AssertionError("expected RagUnavailableError")

    assert repository.recent_limit == 6
    assert repository.saved is None


def test_conversation_service_does_not_expose_another_users_order() -> None:
    repository = FakeConversationRepository()
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,  # type: ignore[arg-type]
        rag_service=rag_service,  # type: ignore[arg-type]
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "查询 WD-10002"))

    assert rag_service.question is None
    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "未找到当前用户的提现订单 WD-10002。"
    )
    assert repository.traces[0]["handling_result"] == "business_withdrawal_not_found"


class FakeConversationService:
    async def create_conversation(self, user_id: str) -> ConversationRecord:
        assert user_id == USER_ID
        return _conversation()

    async def list_conversations(
        self,
        user_id: str,
        *,
        limit: int,
        cursor: ConversationCursor | None,
    ) -> ConversationPage:
        assert user_id == USER_ID
        assert limit == 20
        assert cursor is None
        return ConversationPage(
            items=[
                ConversationSummary(
                    conversation=_conversation(),
                    title="提现没有到账",
                )
            ],
            next_cursor=ConversationCursor(
                updated_at=UPDATED_AT,
                conversation_id=CONVERSATION_ID,
            ),
        )

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


def test_conversation_api_lists_conversations_with_pagination() -> None:
    app.dependency_overrides[get_conversation_service] = FakeConversationService
    app.dependency_overrides[get_current_user] = lambda: CURRENT_USER
    try:
        response = TestClient(app).get("/conversations?limit=20")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [
        {
            "id": str(CONVERSATION_ID),
            "title": "提现没有到账",
            "created_at": CREATED_AT.isoformat().replace("+00:00", "Z"),
            "updated_at": UPDATED_AT.isoformat().replace("+00:00", "Z"),
        }
    ]
    assert _decode_cursor(body["next_cursor"]) == ConversationCursor(
        updated_at=UPDATED_AT,
        conversation_id=CONVERSATION_ID,
    )


def test_conversation_api_rejects_invalid_list_pagination() -> None:
    app.dependency_overrides[get_conversation_service] = FakeConversationService
    app.dependency_overrides[get_current_user] = lambda: CURRENT_USER
    try:
        response = TestClient(app).get("/conversations?limit=101")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_conversation_api_rejects_invalid_cursor() -> None:
    app.dependency_overrides[get_conversation_service] = FakeConversationService
    app.dependency_overrides[get_current_user] = lambda: CURRENT_USER
    try:
        response = TestClient(app).get("/conversations?limit=20&cursor=not-valid")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json() == {"detail": "cursor 无效"}


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
