import asyncio
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from customer_service.auth.api import get_current_user
from customer_service.auth.session import AuthenticatedUser
from customer_service.business.service import MOCK_WITHDRAWAL_SERVICE, WithdrawalRecord
from customer_service.conversations.api import (
    decode_cursor,
    encode_cursor,
    get_conversation_service,
)
from customer_service.conversations.polisher import ConversationAnswerPolisher
from customer_service.conversations.repository import (
    ConversationCursor,
    ConversationHistory,
    ConversationNotFoundError,
    ConversationPage,
    ConversationRecord,
    ConversationRepository,
    ConversationSummary,
    ConversationTurn,
    ConversationTurnTraceRecord,
    MessageRecord,
    conversation_title,
    conversation_turn_traces,
    conversations,
    messages,
)
from customer_service.conversations.service import (
    ConversationService,
    RagUnavailableError,
)
from customer_service.knowledge.chat import ChatMessage
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
DEPOSIT_NOT_FOUND_PREFIX = (
    "未找到当前用户的充值记录 {txid}。"
    "请先确认 TxID、充值网络和到账账户是否正确。"
    "如果链上已成功但仍未到账，请继续补充币种、网络、充值时间和页面提示，"
    "我会根据这些信息继续排查。"
)
DEPOSIT_FOLLOWUP_RECEIVED_PROMPT = (
    "已记录你的补充信息。当前还无法自动确认链上状态或入账原因，"
    "我会把这类情况计入人工兜底候选；如还有截图或更完整页面提示，可以继续补充。"
)
DEPOSIT_FOLLOWUP_NEXT_ACTION = {
    "type": "provide_deposit_followup_details",
    "state": "awaiting_deposit_followup_details",
    "expected_input": "deposit_followup_details",
    "missing_fields": ["coin", "chain", "deposit_time", "page_hint"],
    "manual_fallback_candidate": False,
}


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
    role: Literal["user", "assistant"],
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


def _trace(
    *,
    handling_result: str,
    route: str = "business_query",
    category: str = "deposit",
    intent: str = "missing_arrival",
    missing_fields: tuple[str, ...] = (),
    is_inactive_reset: bool = False,
) -> ConversationTurnTraceRecord:
    return ConversationTurnTraceRecord(
        route=route,
        category=category,
        intent=intent,
        intent_source="rule",
        entities={},
        missing_fields=missing_fields,
        handling_result=handling_result,
        is_inactive_reset=is_inactive_reset,
        created_at=CREATED_AT,
    )


class FakeConversationRepository:
    def __init__(
        self,
        *,
        exists: bool = True,
        recent_messages: list[MessageRecord] | None = None,
        recent_traces: list[ConversationTurnTraceRecord] | None = None,
        trace_error: bool = False,
    ) -> None:
        self.exists = exists
        self.recent_messages = recent_messages or []
        self.recent_traces = recent_traces or []
        self.trace_error = trace_error
        self.recent_limit: int | None = None
        self.recent_trace_limit: int | None = None
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

    async def save_turn(
        self,
        *,
        conversation_id: UUID,
        user_id: str,
        user_content: str,
        assistant_content: str,
        assistant_sources: list[dict[str, str]],
    ) -> ConversationTurn:
        self.saved = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "user_content": user_content,
            "assistant_content": assistant_content,
            "assistant_sources": assistant_sources,
        }
        return _turn()

    async def record_turn_trace(
        self,
        *,
        conversation_id: UUID,
        user_id: str,
        user_message_id: int | None,
        assistant_message_id: int | None,
        route: str,
        category: str,
        intent: str,
        intent_source: str,
        confidence: float,
        entities: dict[str, str],
        missing_fields: tuple[str, ...],
        handling_result: str,
        is_inactive_reset: bool,
    ) -> None:
        if self.trace_error:
            raise SQLAlchemyError("trace database unavailable")
        self.traces.append(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "route": route,
                "category": category,
                "intent": intent,
                "intent_source": intent_source,
                "confidence": confidence,
                "entities": entities,
                "missing_fields": missing_fields,
                "handling_result": handling_result,
                "is_inactive_reset": is_inactive_reset,
            }
        )

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

    async def get_recent_turn_traces(
        self,
        conversation_id: UUID,
        *,
        user_id: str,
        limit: int,
    ) -> list[ConversationTurnTraceRecord]:
        self.checked_user_id = user_id
        self.recent_trace_limit = limit
        return self.recent_traces[-limit:]

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
        self.category: str | None = None

    async def answer(
        self,
        question: str,
        *,
        history: Sequence[RagHistoryMessage] = (),
        category: str | None = None,
    ) -> RagAnswer:
        self.question = question
        self.history = list(history)
        self.category = category
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


class FakeAnswerPolisher:
    def __init__(
        self,
        answer: RagAnswer | None = None,
        *,
        should_raise: bool = False,
    ) -> None:
        self.answer = answer or RagAnswer(answer="润色后的回答。[资料 1]", sources=[])
        self.should_raise = should_raise
        self.calls: list[dict[str, object]] = []

    async def polish(
        self,
        *,
        question: str,
        answer: RagAnswer,
        decision: IntentDecision,
    ) -> RagAnswer:
        self.calls.append(
            {
                "question": question,
                "answer": answer,
                "decision": decision,
            }
        )
        if self.should_raise:
            raise RuntimeError("polish failed")
        return self.answer


class FakePolishChatClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.requests: list[list[ChatMessage]] = []
        self.purposes: list[str] = []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        purpose: str = "chat",
    ) -> str:
        self.requests.append(list(messages))
        self.purposes.append(purpose)
        return self.response


class FakeIntentRecognizer:
    def __init__(self, decision: IntentDecision | None = None) -> None:
        self.decision = decision or IntentDecision(
            route="knowledge_rag",
            category="other",
            intent="unknown",
            confidence=1.0,
            entities={},
            missing_fields=(),
        )
        self.history: list[IntentHistoryMessage] = []

    async def recognize(
        self,
        content: str,
        *,
        history: Sequence[IntentHistoryMessage] = (),
    ) -> IntentDecision:
        self.history = list(history)
        return self.decision


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
        "category",
        "intent",
        "intent_source",
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
        repository=repository,
        rag_service=rag_service,
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
            "category": "other",
            "intent": "unknown",
            "intent_source": "model",
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
        repository=repository,
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
    assert conversation_title(None) == "空白会话"
    assert conversation_title("  提现完成\n但钱包没到账  ") == "提现完成 但钱包没到账"
    assert conversation_title("问" * 25) == f"{'问' * 24}…"


def test_conversation_cursor_round_trip() -> None:
    cursor = ConversationCursor(
        updated_at=UPDATED_AT,
        conversation_id=CONVERSATION_ID,
    )

    assert decode_cursor(encode_cursor(cursor)) == cursor


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
        repository=repository,
        rag_service=rag_service,
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
        repository=repository,
        rag_service=rag_service,
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
        repository=repository,
        rag_service=rag_service,
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
        repository=repository,
        rag_service=rag_service,
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
        repository=repository,
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
    assert repository.traces[0]["category"] == "withdrawal"
    assert repository.traces[0]["intent_source"] == "rule"
    assert repository.traces[0]["entities"] == {"order_id": "WD-10001"}
    assert repository.traces[0]["handling_result"] == "business_withdrawal_found"


def test_conversation_service_requests_order_id_for_platform_hold_query() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    turn = asyncio.run(
        service.send_message(
            USER_ID,
            CONVERSATION_ID,
            "提现被风控卡住了",
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
    assert turn.next_action == {
        "type": "provide_withdrawal_order_id",
        "state": "awaiting_withdrawal_order_id",
        "expected_input": "withdrawal_order_id",
        "missing_fields": ["order_id"],
        "manual_fallback_candidate": False,
    }


def test_conversation_service_answers_completed_withdrawal_as_onchain_transparent() -> None:
    repository = FakeConversationRepository()
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,
        rag_service=rag_service,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    turn = asyncio.run(
        service.send_message(
            USER_ID,
            CONVERSATION_ID,
            "提现完成但钱包没到账怎么办？",
        )
    )

    assert rag_service.question is None
    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "如果平台侧提现订单已完成、已广播或已上链，链上状态通常是透明的。"
        "请通过提现 TxID、区块浏览器、目标地址和网络自行核对链上进度及接收方入账规则。"
        "平台客服主要能确认平台侧是否已经放行；如果订单仍显示审核中、处理中、"
        "风控限制或不放行，请提供提现订单号，我可以帮你查询平台侧状态。"
    )
    assert repository.saved["assistant_sources"] == []
    assert repository.traces[0]["route"] == "knowledge_rag"
    assert repository.traces[0]["category"] == "withdrawal"
    assert repository.traces[0]["intent"] == "onchain_status"
    assert repository.traces[0]["handling_result"] == "withdrawal_onchain_transparent"
    assert turn.next_action is None


def test_conversation_service_reports_pending_withdrawal_as_platform_processing() -> None:
    class PendingWithdrawalService:
        def get_withdrawal(
            self,
            user_id: str,
            order_id: str,
        ) -> WithdrawalRecord | None:
            if user_id != USER_ID or order_id.upper() != "WD-20001":
                return None
            return WithdrawalRecord(
                order_id="WD-20001",
                coin="USDT",
                size="50.00",
                status="pending",
                chain="TRC20",
                updated_at="2026-06-20T13:30:00+08:00",
            )

    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=PendingWithdrawalService(),
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    turn = asyncio.run(
        service.send_message(USER_ID, CONVERSATION_ID, "查询 WD-20001")
    )

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "Mock 查询结果：提现订单 WD-20001，状态 pending，数量 50.00 USDT，"
        "网络 TRC20，更新时间 2026-06-20T13:30:00+08:00。"
        "该订单仍在平台侧处理中，可能涉及审核、风控或合规检查；"
        "具体原因以页面提示或平台审核结果为准。"
    )
    assert repository.traces[0]["handling_result"] == (
        "business_withdrawal_pending_review"
    )
    assert turn.next_action == {
        "type": "provide_withdrawal_review_details",
        "state": "manual_fallback_candidate",
        "expected_input": "withdrawal_review_details",
        "missing_fields": ["page_hint"],
        "manual_fallback_candidate": True,
    }


def test_conversation_service_requests_txid_for_deposit_query() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    turn = asyncio.run(
        service.send_message(USER_ID, CONVERSATION_ID, "我的充值一直没到账")
    )

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "请提供充值 TxID，例如 TX-10001，我可以帮你查询充值处理状态。"
    )
    assert repository.saved["assistant_sources"] == []
    assert repository.traces[0]["category"] == "deposit"
    assert repository.traces[0]["handling_result"] == "missing_deposit_txid"
    assert turn.next_action == {
        "type": "provide_deposit_txid",
        "state": "awaiting_deposit_txid",
        "expected_input": "deposit_txid",
        "missing_fields": ["txid"],
        "manual_fallback_candidate": False,
    }


def test_conversation_service_routes_deposit_knowledge_query_to_rag() -> None:
    repository = FakeConversationRepository()
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,
        rag_service=rag_service,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    asyncio.run(
        service.send_message(
            USER_ID,
            CONVERSATION_ID,
            "USDT 走 TRC20，今天 14:30 充值需要多少个区块确认",
        )
    )

    assert rag_service.question == "USDT 走 TRC20，今天 14:30 充值需要多少个区块确认"
    assert repository.saved is not None
    assert repository.saved["assistant_sources"] == [
        {
            "article_id": "article-1",
            "title": "提现没有到账",
            "source_url": "https://example.com/article-1",
        }
    ]
    assert repository.traces[0]["route"] == "knowledge_rag"
    assert repository.traces[0]["category"] == "deposit"
    assert repository.traces[0]["intent"] == "rule"
    assert repository.traces[0]["entities"] == {
        "coin": "USDT",
        "network": "TRC20",
        "time_hint": "今天 14:30",
    }
    assert repository.traces[0]["handling_result"] == "rag_answer"


@pytest.mark.parametrize(
    (
        "decision",
        "expected_content",
        "expected_sources",
        "expected_handling_result",
        "expected_knowledge_category",
    ),
    [
        (
            IntentDecision(
                route="knowledge_rag",
                category="deposit",
                intent="rule",
                confidence=1.0,
                entities={},
                missing_fields=(),
            ),
            "请查询 TxID。[资料 1]",
            [
                {
                    "article_id": "article-1",
                    "title": "提现没有到账",
                    "source_url": "https://example.com/article-1",
                }
            ],
            "rag_answer",
            "充值与提现",
        ),
        (
            IntentDecision(
                route="knowledge_rag",
                category="deposit",
                intent="memo_tag_issue",
                confidence=1.0,
                entities={},
                missing_fields=(),
            ),
            "请查询 TxID。[资料 1]",
            [
                {
                    "article_id": "article-1",
                    "title": "提现没有到账",
                    "source_url": "https://example.com/article-1",
                }
            ],
            "rag_answer",
            "充值与提现",
        ),
        (
            IntentDecision(
                route="knowledge_rag",
                category="identity_verification",
                intent="verification_failure",
                confidence=1.0,
                entities={},
                missing_fields=(),
            ),
            "请查询 TxID。[资料 1]",
            [
                {
                    "article_id": "article-1",
                    "title": "提现没有到账",
                    "source_url": "https://example.com/article-1",
                }
            ],
            "rag_answer",
            "身份认证",
        ),
        (
            IntentDecision(
                route="knowledge_rag",
                category="account_security",
                intent="compromised",
                confidence=1.0,
                entities={},
                missing_fields=(),
            ),
            "请查询 TxID。[资料 1]",
            [
                {
                    "article_id": "article-1",
                    "title": "提现没有到账",
                    "source_url": "https://example.com/article-1",
                }
            ],
            "rag_answer",
            "账户与安全",
        ),
        (
            IntentDecision(
                route="knowledge_rag",
                category="spot_trading",
                intent="trading_question",
                confidence=1.0,
                entities={},
                missing_fields=(),
            ),
            "请查询 TxID。[资料 1]",
            [
                {
                    "article_id": "article-1",
                    "title": "提现没有到账",
                    "source_url": "https://example.com/article-1",
                }
            ],
            "rag_answer",
            "现货交易",
        ),
        (
            IntentDecision(
                route="knowledge_rag",
                category="general_platform",
                intent="platform_operation",
                confidence=1.0,
                entities={},
                missing_fields=(),
            ),
            "请查询 TxID。[资料 1]",
            [
                {
                    "article_id": "article-1",
                    "title": "提现没有到账",
                    "source_url": "https://example.com/article-1",
                }
            ],
            "rag_answer",
            None,
        ),
        (
            IntentDecision(
                route="human_request",
                category="other",
                intent="human_only",
                confidence=1.0,
                entities={},
                missing_fields=(),
            ),
            (
                "已记录你的人工客服诉求。请通过平台 App 或网页端的官方在线客服入口联系人工客服；"
                "也可以补充具体问题、订单号或页面提示，方便继续处理。"
            ),
            [],
            "human_request",
            None,
        ),
        (
            IntentDecision(
                route="out_of_scope",
                category="other",
                intent="out_of_scope",
                confidence=1.0,
                entities={},
                missing_fields=(),
            ),
            "我目前只能处理交易所账户、交易和平台使用相关的问题。",
            [],
            "out_of_scope",
            None,
        ),
        (
            IntentDecision(
                route="unknown",
                category="other",
                intent="unknown",
                confidence=1.0,
                entities={},
                missing_fields=(),
            ),
            "请补充说明你遇到的具体问题、操作步骤或页面提示。",
            [],
            "unknown",
            None,
        ),
    ],
)
def test_conversation_service_routes_intent_handling_matrix(
    decision: IntentDecision,
    expected_content: str,
    expected_sources: list[dict[str, str]],
    expected_handling_result: str,
    expected_knowledge_category: str | None,
) -> None:
    repository = FakeConversationRepository()
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,
        rag_service=rag_service,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=FakeIntentRecognizer(decision),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "测试问题"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == expected_content
    assert repository.saved["assistant_sources"] == expected_sources
    assert repository.traces[0]["route"] == decision.route
    assert repository.traces[0]["category"] == decision.category
    assert repository.traces[0]["intent"] == decision.intent
    assert repository.traces[0]["handling_result"] == expected_handling_result
    assert rag_service.category == expected_knowledge_category


def test_conversation_service_uses_fixed_answer_for_human_support_request() -> None:
    repository = FakeConversationRepository()
    rag_service = FakeRagService()
    service = ConversationService(
        repository=repository,
        rag_service=rag_service,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    turn = asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "我要找人工客服"))

    assert rag_service.question is None
    assert rag_service.category is None
    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "已记录你的人工客服诉求。请通过平台 App 或网页端的官方在线客服入口联系人工客服；"
        "也可以补充具体问题、订单号或页面提示，方便继续处理。"
    )
    assert repository.saved["assistant_sources"] == []
    assert repository.traces[0]["handling_result"] == "human_request"
    assert turn.next_action == {
        "type": "clarify_problem",
        "state": "awaiting_problem_description",
        "expected_input": "problem_description",
        "missing_fields": ["problem_description"],
        "manual_fallback_candidate": False,
    }


def test_conversation_service_polishes_identity_failure_rag_answer() -> None:
    repository = FakeConversationRepository()
    rag_service = FakeRagService()
    polished_answer = RagAnswer(
        answer="身份认证失败可能和照片质量有关。[资料 1]",
        sources=[
            RagSource(
                article_id="article-1",
                title="身份认证失败",
                source_url="https://example.com/identity",
            )
        ],
    )
    polisher = FakeAnswerPolisher(polished_answer)
    decision = IntentDecision(
        route="knowledge_rag",
        category="identity_verification",
        intent="verification_failure",
        confidence=1.0,
        entities={},
        missing_fields=(),
    )
    service = ConversationService(
        repository=repository,
        rag_service=rag_service,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=FakeIntentRecognizer(decision),
        answer_polisher=polisher,
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "我的身份认证失败了"))

    assert len(polisher.calls) == 1
    assert polisher.calls[0]["question"] == "我的身份认证失败了"
    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "身份认证失败可能和照片质量有关。[资料 1]"
    )
    assert repository.saved["assistant_sources"] == [
        {
            "article_id": "article-1",
            "title": "身份认证失败",
            "source_url": "https://example.com/identity",
        }
    ]
    assert repository.traces[0]["handling_result"] == "rag_answer"


def test_conversation_service_does_not_polish_other_rag_answers() -> None:
    repository = FakeConversationRepository()
    rag_service = FakeRagService()
    polisher = FakeAnswerPolisher()
    decision = IntentDecision(
        route="knowledge_rag",
        category="spot_trading",
        intent="trading_question",
        confidence=1.0,
        entities={},
        missing_fields=(),
    )
    service = ConversationService(
        repository=repository,
        rag_service=rag_service,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=FakeIntentRecognizer(decision),
        answer_polisher=polisher,
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "限价单为什么没成交"))

    assert polisher.calls == []
    assert repository.saved is not None
    assert repository.saved["assistant_content"] == "请查询 TxID。[资料 1]"


def test_conversation_service_keeps_original_rag_answer_when_polish_fails() -> None:
    repository = FakeConversationRepository()
    rag_service = FakeRagService()
    polisher = FakeAnswerPolisher(should_raise=True)
    decision = IntentDecision(
        route="knowledge_rag",
        category="identity_verification",
        intent="verification_failure",
        confidence=1.0,
        entities={},
        missing_fields=(),
    )
    service = ConversationService(
        repository=repository,
        rag_service=rag_service,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=FakeIntentRecognizer(decision),
        answer_polisher=polisher,
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "身份认证失败"))

    assert len(polisher.calls) == 1
    assert repository.saved is not None
    assert repository.saved["assistant_content"] == "请查询 TxID。[资料 1]"
    assert repository.traces[0]["handling_result"] == "rag_answer"


def test_conversation_answer_polisher_uses_chat_client() -> None:
    chat_client = FakePolishChatClient("你的身份证没问题，也可能因为照片不清晰失败。[资料 1]")
    polisher = ConversationAnswerPolisher(chat_client)
    original = RagAnswer(
        answer="身份认证失败的原因包括文件质量低。[资料 1]",
        sources=[
            RagSource(
                article_id="article-1",
                title="身份认证失败",
                source_url="https://example.com/identity",
            )
        ],
    )
    decision = IntentDecision(
        route="knowledge_rag",
        category="identity_verification",
        intent="verification_failure",
        confidence=1.0,
        entities={},
        missing_fields=(),
    )

    result = asyncio.run(
        polisher.polish(
            question="我的身份证没问题，为什么认证失败",
            answer=original,
            decision=decision,
        )
    )

    assert result.answer == "你的身份证没问题，也可能因为照片不清晰失败。[资料 1]"
    assert result.sources == original.sources
    assert chat_client.purposes == ["answer_polish"]
    assert "只润色表达，不新增事实" in chat_client.requests[0][0]["content"]
    assert "用户问题：我的身份证没问题，为什么认证失败" in (
        chat_client.requests[0][1]["content"]
    )
    assert "待润色回答" in chat_client.requests[0][1]["content"]


def test_conversation_answer_polisher_keeps_original_when_citation_is_dropped() -> None:
    chat_client = FakePolishChatClient("你的身份证没问题，也可能因为照片不清晰失败。")
    polisher = ConversationAnswerPolisher(chat_client)
    original = RagAnswer(
        answer="身份认证失败的原因包括文件质量低。[资料 1]",
        sources=[
            RagSource(
                article_id="article-1",
                title="身份认证失败",
                source_url="https://example.com/identity",
            )
        ],
    )
    decision = IntentDecision(
        route="knowledge_rag",
        category="identity_verification",
        intent="verification_failure",
        confidence=1.0,
        entities={},
        missing_fields=(),
    )

    result = asyncio.run(
        polisher.polish(
            question="我的身份证没问题，为什么认证失败",
            answer=original,
            decision=decision,
        )
    )

    assert result == original
    assert chat_client.purposes == ["answer_polish"]


def test_conversation_service_falls_back_when_human_support_rag_unavailable() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    turn = asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "我要找人工客服"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "已记录你的人工客服诉求。请通过平台 App 或网页端的官方在线客服入口联系人工客服；"
        "也可以补充具体问题、订单号或页面提示，方便继续处理。"
    )
    assert repository.traces[0]["handling_result"] == "human_request"
    assert turn.next_action == {
        "type": "clarify_problem",
        "state": "awaiting_problem_description",
        "expected_input": "problem_description",
        "missing_fields": ["problem_description"],
        "manual_fallback_candidate": False,
    }


def test_conversation_service_routes_deposit_txid_to_business_query() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
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
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    turn = asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "帮我查 TX-10002"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == DEPOSIT_NOT_FOUND_PREFIX.format(
        txid="TX-10002"
    )
    assert repository.saved["assistant_sources"] == []
    assert repository.traces[0]["handling_result"] == "business_deposit_not_found"
    assert turn.next_action == DEPOSIT_FOLLOWUP_NEXT_ACTION


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
        repository=repository,
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
    assert repository.traces[0]["intent_source"] == "fallback"


def test_conversation_service_completes_numeric_txid_in_pending_context() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            _message(
                message_id=1,
                role="assistant",
                content="请提供充值 TxID，例如 TX-10001，我可以帮你查询充值处理状态。",
                created_at=CREATED_AT,
            )
        ]
    )
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "10001"))

    assert repository.saved is not None
    assert repository.saved["user_content"] == "10001"
    assert repository.saved["assistant_content"] == (
        "Mock 查询结果：充值 TxID TX-10001，状态 success，数量 88.00 USDT，"
        "网络 TRC20，更新时间 2026-06-20T12:30:00+08:00。"
    )
    assert repository.traces[0]["entities"] == {"txid": "TX-10001"}
    assert repository.traces[0]["handling_result"] == "business_deposit_found"


def test_conversation_service_completes_numeric_txid_from_pending_trace() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            _message(
                message_id=1,
                role="assistant",
                content="请补充一下编号。",
                created_at=CREATED_AT,
            )
        ],
        recent_traces=[
            _trace(
                handling_result="missing_deposit_txid",
                missing_fields=("txid",),
            )
        ],
    )
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "10001"))

    assert repository.recent_trace_limit == 3
    assert repository.saved is not None
    assert repository.saved["user_content"] == "10001"
    assert repository.traces[0]["entities"] == {"txid": "TX-10001"}
    assert repository.traces[0]["handling_result"] == "business_deposit_found"


def test_conversation_service_guides_deposit_followup_when_numeric_txid_not_found() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            _message(
                message_id=1,
                role="assistant",
                content="请提供充值 TxID，例如 TX-10001，我可以帮你查询充值处理状态。",
                created_at=CREATED_AT,
            )
        ]
    )
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    turn = asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "10002"))

    assert repository.saved is not None
    assert repository.saved["user_content"] == "10002"
    assert repository.saved["assistant_content"] == DEPOSIT_NOT_FOUND_PREFIX.format(
        txid="TX-10002"
    )
    assert repository.traces[0]["entities"] == {"txid": "TX-10002"}
    assert repository.traces[0]["handling_result"] == "business_deposit_not_found"
    assert turn.next_action == DEPOSIT_FOLLOWUP_NEXT_ACTION


def test_conversation_service_records_deposit_followup_details_from_trace() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            _message(
                message_id=1,
                role="assistant",
                content="没有查到这笔充值。",
                created_at=CREATED_AT,
            )
        ],
        recent_traces=[
            _trace(
                handling_result="business_deposit_not_found",
                route="business_query",
                category="deposit",
                intent="status_query",
            )
        ],
    )
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    turn = asyncio.run(
        service.send_message(
            USER_ID,
            CONVERSATION_ID,
            "USDT，TRC20，今天 14:30，页面显示链上成功但未到账",
        )
    )

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == DEPOSIT_FOLLOWUP_RECEIVED_PROMPT
    assert repository.traces[0]["route"] == "human_request"
    assert repository.traces[0]["category"] == "deposit"
    assert repository.traces[0]["intent"] == "followup_details"
    assert repository.traces[0]["intent_source"] == "fallback"
    assert turn.next_action == {
        "type": "clarify_problem",
        "state": "manual_fallback_candidate",
        "expected_input": "problem_description",
        "missing_fields": ["problem_description"],
        "manual_fallback_candidate": True,
    }


def test_conversation_service_records_deposit_followup_details() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            _message(
                message_id=1,
                role="assistant",
                content=DEPOSIT_NOT_FOUND_PREFIX.format(txid="TX-10002"),
                created_at=CREATED_AT,
            )
        ]
    )
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    turn = asyncio.run(
        service.send_message(
            USER_ID,
            CONVERSATION_ID,
            "USDT，TRC20，今天 14:30，页面显示链上成功但未到账",
        )
    )

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == DEPOSIT_FOLLOWUP_RECEIVED_PROMPT
    assert repository.traces[0]["route"] == "human_request"
    assert repository.traces[0]["category"] == "deposit"
    assert repository.traces[0]["intent"] == "followup_details"
    assert repository.traces[0]["intent_source"] == "fallback"
    assert repository.traces[0]["handling_result"] == "deposit_followup_received"
    assert turn.next_action == {
        "type": "clarify_problem",
        "state": "manual_fallback_candidate",
        "expected_input": "problem_description",
        "missing_fields": ["problem_description"],
        "manual_fallback_candidate": True,
    }


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
        repository=repository,
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
        repository=repository,
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


def test_conversation_service_completes_numeric_order_id_in_pending_context() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            _message(
                message_id=1,
                role="assistant",
                content="请提供提现订单号，例如 WD-10001，我可以帮你查询处理状态。",
                created_at=CREATED_AT,
            )
        ]
    )
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "10001"))

    assert repository.saved is not None
    assert repository.saved["user_content"] == "10001"
    assert "提现订单 WD-10001" in str(repository.saved["assistant_content"])
    assert repository.traces[0]["entities"] == {"order_id": "WD-10001"}
    assert repository.traces[0]["handling_result"] == "business_withdrawal_found"


def test_conversation_service_completes_numeric_order_id_from_pending_trace() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            _message(
                message_id=1,
                role="assistant",
                content="请补充一下编号。",
                created_at=CREATED_AT,
            )
        ],
        recent_traces=[
            _trace(
                handling_result="missing_withdrawal_order_id",
                category="withdrawal",
                missing_fields=("order_id",),
            )
        ],
    )
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "10001"))

    assert repository.saved is not None
    assert repository.saved["user_content"] == "10001"
    assert repository.traces[0]["entities"] == {"order_id": "WD-10001"}
    assert repository.traces[0]["handling_result"] == "business_withdrawal_found"


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
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "查询 WD-10001"))

    assert repository.saved is not None
    assert "提现订单 WD-10001" in str(repository.saved["assistant_content"])


def test_conversation_service_does_not_override_human_request_with_pending_trace() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            _message(
                message_id=1,
                role="assistant",
                content="请补充一下编号。",
                created_at=CREATED_AT,
            )
        ],
        recent_traces=[
            _trace(
                handling_result="missing_deposit_txid",
                route="knowledge_rag",
                category="deposit",
                intent="missing_arrival",
                missing_fields=("txid",),
            )
        ],
    )
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    turn = asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "人工客服"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "已记录你的人工客服诉求。请通过平台 App 或网页端的官方在线客服入口联系人工客服；"
        "也可以补充具体问题、订单号或页面提示，方便继续处理。"
    )
    assert repository.traces[0]["route"] == "human_request"
    assert repository.traces[0]["category"] == "other"
    assert repository.traces[0]["intent"] == "human_only"
    assert repository.traces[0]["handling_result"] == "human_request"
    assert turn.next_action == {
        "type": "clarify_problem",
        "state": "awaiting_problem_description",
        "expected_input": "problem_description",
        "missing_fields": ["problem_description"],
        "manual_fallback_candidate": False,
    }


def test_conversation_service_uses_intent_rules_in_production_path() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
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
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    turn = asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "这个怎么弄"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "请补充说明你遇到的具体问题、操作步骤或页面提示。"
    )
    assert repository.traces[0]["route"] == "unknown"
    assert repository.traces[0]["intent_source"] == "fallback"
    assert repository.traces[0]["handling_result"] == "unknown"
    assert turn.next_action == {
        "type": "clarify_problem",
        "state": "awaiting_problem_description",
        "expected_input": "problem_description",
        "missing_fields": ["problem_description"],
        "manual_fallback_candidate": False,
    }


def test_conversation_service_marks_repeated_unknown_as_manual_fallback_candidate() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            _message(message_id=1, role="user", content="这个怎么弄"),
            _message(
                message_id=2,
                role="assistant",
                content="请补充说明你遇到的具体问题、操作步骤或页面提示。",
            ),
        ]
    )
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    turn = asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "还是不行"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "我还无法判断具体问题。请从提现、充值、身份认证、账户安全中选择一个方向，"
        "或直接发送订单号/TxID；本次未解决情况已记录用于后续兜底统计。"
    )
    assert repository.traces[0]["handling_result"] == "manual_fallback_candidate"
    assert turn.next_action == {
        "type": "clarify_problem",
        "state": "manual_fallback_candidate",
        "expected_input": "problem_description",
        "missing_fields": ["problem_description"],
        "manual_fallback_candidate": True,
    }


def test_conversation_service_marks_repeated_human_request_as_manual_fallback_candidate() -> None:
    repository = FakeConversationRepository(
        recent_messages=[
            _message(message_id=1, role="user", content="人工"),
            _message(
                message_id=2,
                role="assistant",
                content="请先描述需要解决的具体问题，我会优先尝试自动查询或提供处理方案。",
            ),
        ]
    )
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
        now=lambda: datetime(2026, 6, 19, 8, 2, tzinfo=UTC),
    )

    turn = asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "人工客服"))

    assert repository.saved is not None
    assert repository.saved["assistant_content"] == (
        "我已记录你需要人工兜底的诉求。请继续补充具体问题、订单号或页面提示，"
        "我会先尝试自动处理，无法处理的情况会进入兜底统计。"
    )
    assert repository.traces[0]["handling_result"] == "manual_fallback_candidate"
    assert turn.next_action == {
        "type": "clarify_problem",
        "state": "manual_fallback_candidate",
        "expected_input": "problem_description",
        "missing_fields": ["problem_description"],
        "manual_fallback_candidate": True,
    }


def test_conversation_service_keeps_response_when_trace_write_fails() -> None:
    repository = FakeConversationRepository(trace_error=True)
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    turn = asyncio.run(service.send_message(USER_ID, CONVERSATION_ID, "查询 WD-10001"))

    assert turn.assistant_message.role == "assistant"
    assert repository.saved is not None
    assert "提现订单 WD-10001" in str(repository.saved["assistant_content"])
    assert repository.traces == []


def test_conversation_service_restores_history_next_action_from_trace() -> None:
    repository = FakeConversationRepository(
        recent_traces=[
            _trace(
                handling_result="missing_deposit_txid",
                route="knowledge_rag",
                category="deposit",
                intent="missing_arrival",
                missing_fields=("txid",),
            )
        ]
    )
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    history = asyncio.run(service.get_history(USER_ID, CONVERSATION_ID))

    assert repository.recent_trace_limit == 1
    assert history.next_action == {
        "type": "provide_deposit_txid",
        "state": "awaiting_deposit_txid",
        "expected_input": "deposit_txid",
        "missing_fields": ["txid"],
        "manual_fallback_candidate": False,
    }


def test_conversation_service_does_not_restore_history_next_action_for_completed_trace() -> None:
    repository = FakeConversationRepository(
        recent_traces=[
            _trace(
                handling_result="business_deposit_found",
                route="business_query",
                category="deposit",
                intent="status_query",
            )
        ]
    )
    service = ConversationService(
        repository=repository,
        rag_service=None,
        withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
        intent_service=IntentService(None),
    )

    history = asyncio.run(service.get_history(USER_ID, CONVERSATION_ID))

    assert history.next_action is None


def test_conversation_service_rejects_rag_question_when_unconfigured() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(
        repository=repository,
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
        repository=repository,
        rag_service=rag_service,
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
        return ConversationTurn(
            user_message=_turn().user_message,
            assistant_message=_turn().assistant_message,
            next_action={
                "type": "provide_withdrawal_order_id",
                "state": "awaiting_withdrawal_order_id",
                "expected_input": "withdrawal_order_id",
                "missing_fields": ["order_id"],
                "manual_fallback_candidate": False,
            },
        )

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
            next_action={
                "type": "provide_withdrawal_order_id",
                "state": "awaiting_withdrawal_order_id",
                "expected_input": "withdrawal_order_id",
                "missing_fields": ["order_id"],
                "manual_fallback_candidate": False,
            },
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
    assert send_response.json()["assistant_message"]["next_action"] == {
        "type": "provide_withdrawal_order_id",
        "state": "awaiting_withdrawal_order_id",
        "expected_input": "withdrawal_order_id",
        "missing_fields": ["order_id"],
        "manual_fallback_candidate": False,
    }
    assert history_response.status_code == 200
    assert [message["role"] for message in history_response.json()["messages"]] == [
        "user",
        "assistant",
    ]
    assert history_response.json()["messages"][-1]["next_action"] == {
        "type": "provide_withdrawal_order_id",
        "state": "awaiting_withdrawal_order_id",
        "expected_input": "withdrawal_order_id",
        "missing_fields": ["order_id"],
        "manual_fallback_candidate": False,
    }


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
    assert decode_cursor(body["next_cursor"]) == ConversationCursor(
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
