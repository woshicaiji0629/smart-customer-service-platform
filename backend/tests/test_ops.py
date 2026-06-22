from datetime import UTC, datetime

from fastapi.testclient import TestClient

from customer_service.auth.api import get_current_user
from customer_service.auth.session import AuthenticatedUser
from customer_service.main import app
from customer_service.ops.api import get_ops_repository
from customer_service.ops.repository import (
    TraceBreakdown,
    TraceCount,
    TraceSample,
    TraceSummary,
)
from script.summarize_conversation_traces import (
    DEFAULT_SAMPLE_HANDLING_RESULTS,
    _sample_candidate,
    _sample_output,
    _time_range_from_args,
)


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
            total_turns=5,
            by_intent_source=[
                TraceCount(key="rule", count=4),
                TraceCount(key="fallback", count=1),
            ],
            by_route=[
                TraceCount(key="business_query", count=1),
                TraceCount(key="knowledge_rag", count=3),
                TraceCount(key="unknown", count=1),
            ],
            by_handling_result=[
                TraceCount(key="rag_answer", count=2),
                TraceCount(key="business_withdrawal_pending_review", count=1),
                TraceCount(key="withdrawal_onchain_transparent", count=1),
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
                ),
                TraceBreakdown(
                    route="business_query",
                    category="withdrawal",
                    intent="status_query",
                    handling_result="business_withdrawal_pending_review",
                    intent_source="rule",
                    count=1,
                ),
                TraceBreakdown(
                    route="knowledge_rag",
                    category="withdrawal",
                    intent="onchain_status",
                    handling_result="withdrawal_onchain_transparent",
                    intent_source="rule",
                    count=1,
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
        "total_turns": 5,
        "by_intent_source": [
            {"key": "rule", "count": 4},
            {"key": "fallback", "count": 1},
        ],
        "by_route": [
            {"key": "business_query", "count": 1},
            {"key": "knowledge_rag", "count": 3},
            {"key": "unknown", "count": 1},
        ],
        "by_handling_result": [
            {"key": "rag_answer", "count": 2},
            {"key": "business_withdrawal_pending_review", "count": 1},
            {"key": "withdrawal_onchain_transparent", "count": 1},
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
            },
            {
                "route": "business_query",
                "category": "withdrawal",
                "intent": "status_query",
                "handling_result": "business_withdrawal_pending_review",
                "intent_source": "rule",
                "count": 1,
            },
            {
                "route": "knowledge_rag",
                "category": "withdrawal",
                "intent": "onchain_status",
                "handling_result": "withdrawal_onchain_transparent",
                "intent_source": "rule",
                "count": 1,
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
        sample_limit = 20
        sample_format = "candidate"

    start, end = _time_range_from_args(Args())

    assert start == CREATED_AT
    assert end == UPDATED_AT

    class PartialArgs:
        hours = 24
        start_ts = int(CREATED_AT.timestamp())
        end_ts = None
        limit = 20
        sample_limit = 20
        sample_format = "candidate"

    try:
        _time_range_from_args(PartialArgs())
    except ValueError as exc:
        assert "同时提供" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_trace_sample_candidate_serializes_evaluation_context() -> None:
    sample = TraceSample(
        user_content="还是不行",
        route="unknown",
        category="other",
        intent="unknown",
        intent_source="fallback",
        confidence=0,
        entities={},
        missing_fields=[],
        handling_result="manual_fallback_candidate",
        created_at=CREATED_AT,
    )

    assert _sample_candidate(sample) == {
        "query": "还是不行",
        "observed_route": "unknown",
        "observed_category": "other",
        "observed_intent": "unknown",
        "observed_handling_result": "manual_fallback_candidate",
        "intent_source": "fallback",
        "confidence": 0,
        "entities": {},
        "missing_fields": [],
        "created_at": CREATED_AT.isoformat(),
    }


def test_trace_sample_output_can_render_intent_case_draft() -> None:
    sample = TraceSample(
        user_content="还是不行",
        route="unknown",
        category="other",
        intent="unknown",
        intent_source="fallback",
        confidence=0,
        entities={},
        missing_fields=[],
        handling_result="manual_fallback_candidate",
        created_at=CREATED_AT,
    )

    assert _sample_output(sample, "intent-case-draft") == {
        "id": "trace_manual_fallback_candidate_20260619080000_7665209c",
        "query": "还是不行",
        "expected_route": "unknown",
        "expected_category": "other",
        "expected_intent": "unknown",
        "expected_entities": {},
        "expected_missing_fields": [],
        "_review": {
            "observed_handling_result": "manual_fallback_candidate",
            "intent_source": "fallback",
            "confidence": 0,
            "created_at": CREATED_AT.isoformat(),
        },
    }


def test_trace_sample_defaults_include_review_targets() -> None:
    assert DEFAULT_SAMPLE_HANDLING_RESULTS == (
        "unknown",
        "manual_fallback_candidate",
        "deposit_followup_received",
        "business_withdrawal_pending_review",
        "withdrawal_onchain_transparent",
    )
