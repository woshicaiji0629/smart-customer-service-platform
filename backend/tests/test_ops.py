from datetime import UTC, datetime

from fastapi.testclient import TestClient

from customer_service.auth.api import get_current_user
from customer_service.auth.session import AuthenticatedUser
from customer_service.main import app
from customer_service.ops.api import get_ops_repository
from customer_service.ops.repository import TraceBreakdown, TraceCount, TraceSummary
from script.summarize_conversation_traces import _time_range_from_args


CREATED_AT = datetime(2026, 6, 19, 8, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 6, 19, 8, 1, tzinfo=UTC)
CURRENT_USER = AuthenticatedUser("10001", "模拟用户 Alice")


class FakeOpsRepository:
    async def summarize_conversation_traces(
        self,
        *,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> TraceSummary:
        assert start == CREATED_AT
        assert end == UPDATED_AT
        assert limit == 5
        return TraceSummary(
            total_turns=3,
            by_intent_source=[
                TraceCount(key="rule", count=2),
                TraceCount(key="fallback", count=1),
            ],
            by_route=[
                TraceCount(key="knowledge_rag", count=2),
                TraceCount(key="unknown", count=1),
            ],
            by_handling_result=[
                TraceCount(key="rag_answer", count=2),
                TraceCount(key="unknown", count=1),
            ],
            top_breakdowns=[
                TraceBreakdown(
                    route="knowledge_rag",
                    category="deposit",
                    intent="missing_arrival",
                    handling_result="rag_answer",
                    intent_source="rule",
                    count=2,
                )
            ],
        )


def test_ops_api_returns_conversation_trace_summary() -> None:
    app.dependency_overrides[get_ops_repository] = lambda: FakeOpsRepository()
    app.dependency_overrides[get_current_user] = lambda: CURRENT_USER
    try:
        response = TestClient(app).get(
            "/ops/conversation-traces/summary",
            params={
                "start_ts": int(CREATED_AT.timestamp()),
                "end_ts": int(UPDATED_AT.timestamp()),
                "limit": 5,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "start_ts": int(CREATED_AT.timestamp()),
        "end_ts": int(UPDATED_AT.timestamp()),
        "total_turns": 3,
        "by_intent_source": [
            {"key": "rule", "count": 2},
            {"key": "fallback", "count": 1},
        ],
        "by_route": [
            {"key": "knowledge_rag", "count": 2},
            {"key": "unknown", "count": 1},
        ],
        "by_handling_result": [
            {"key": "rag_answer", "count": 2},
            {"key": "unknown", "count": 1},
        ],
        "top_breakdowns": [
            {
                "route": "knowledge_rag",
                "category": "deposit",
                "intent": "missing_arrival",
                "handling_result": "rag_answer",
                "intent_source": "rule",
                "count": 2,
            }
        ],
    }


def test_ops_api_rejects_invalid_trace_summary_range() -> None:
    app.dependency_overrides[get_ops_repository] = lambda: FakeOpsRepository()
    app.dependency_overrides[get_current_user] = lambda: CURRENT_USER
    try:
        client = TestClient(app)
        partial_response = client.get(
            "/ops/conversation-traces/summary",
            params={"start_ts": int(CREATED_AT.timestamp())},
        )
        inverted_response = client.get(
            "/ops/conversation-traces/summary",
            params={
                "start_ts": int(UPDATED_AT.timestamp()),
                "end_ts": int(CREATED_AT.timestamp()),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert partial_response.status_code == 400
    assert partial_response.json() == {
        "detail": "start_ts 和 end_ts 必须同时提供"
    }
    assert inverted_response.status_code == 400
    assert inverted_response.json() == {"detail": "start_ts 必须小于 end_ts"}


def test_trace_summary_script_validates_time_range_args() -> None:
    class Args:
        hours = 24
        start_ts = int(CREATED_AT.timestamp())
        end_ts = int(UPDATED_AT.timestamp())
        limit = 20

    start, end = _time_range_from_args(Args())

    assert start == CREATED_AT
    assert end == UPDATED_AT

    class PartialArgs:
        hours = 24
        start_ts = int(CREATED_AT.timestamp())
        end_ts = None
        limit = 20

    try:
        _time_range_from_args(PartialArgs())
    except ValueError as exc:
        assert "同时提供" in str(exc)
    else:
        raise AssertionError("expected ValueError")
