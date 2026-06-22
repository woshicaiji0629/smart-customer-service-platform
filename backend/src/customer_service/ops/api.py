"""HTTP APIs for operations metrics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from customer_service.auth.api import CurrentUserDependency
from customer_service.ops.repository import (
    OpsRepository,
    TraceBreakdown,
    TraceCount,
    TraceSummary,
)


router = APIRouter(prefix="/ops", tags=["ops"])


class TraceCountResponse(BaseModel):
    key: str
    count: int


class TraceBreakdownResponse(BaseModel):
    route: str
    category: str
    intent: str
    handling_result: str
    intent_source: str
    count: int


class TraceSummaryResponse(BaseModel):
    start_ts: int
    end_ts: int
    total_turns: int
    by_intent_source: list[TraceCountResponse]
    by_route: list[TraceCountResponse]
    by_handling_result: list[TraceCountResponse]
    top_breakdowns: list[TraceBreakdownResponse]


def get_ops_repository(request: Request) -> OpsRepository:
    repository: OpsRepository | None = getattr(
        request.app.state,
        "ops_repository",
        None,
    )
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="运营统计服务未配置",
        )
    return repository


OpsRepositoryDependency = Annotated[OpsRepository, Depends(get_ops_repository)]


@router.get("/conversation-traces/summary", response_model=TraceSummaryResponse)
async def summarize_conversation_traces(
    repository: OpsRepositoryDependency,
    user: CurrentUserDependency,
    start_ts: Annotated[int | None, Query(ge=0)] = None,
    end_ts: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TraceSummaryResponse:
    del user
    start, end = _time_range_from_query(start_ts=start_ts, end_ts=end_ts)
    if start >= end:
        raise HTTPException(status_code=400, detail="start_ts 必须小于 end_ts")
    try:
        summary = await repository.summarize_conversation_traces(
            start=start,
            end=end,
            limit=limit,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="运营统计数据库暂时不可用",
        ) from exc
    return _trace_summary_response(summary, start=start, end=end)


def _time_range_from_query(
    *,
    start_ts: int | None,
    end_ts: int | None,
) -> tuple[datetime, datetime]:
    if (start_ts is None) != (end_ts is None):
        raise HTTPException(
            status_code=400,
            detail="start_ts 和 end_ts 必须同时提供",
        )
    if start_ts is None and end_ts is None:
        end = datetime.now(UTC).replace(microsecond=0)
        return end - timedelta(hours=24), end
    assert start_ts is not None and end_ts is not None
    return datetime.fromtimestamp(start_ts, UTC), datetime.fromtimestamp(end_ts, UTC)


def _trace_summary_response(
    summary: TraceSummary,
    *,
    start: datetime,
    end: datetime,
) -> TraceSummaryResponse:
    return TraceSummaryResponse(
        start_ts=int(start.timestamp()),
        end_ts=int(end.timestamp()),
        total_turns=summary.total_turns,
        by_intent_source=[
            _trace_count_response(item) for item in summary.by_intent_source
        ],
        by_route=[_trace_count_response(item) for item in summary.by_route],
        by_handling_result=[
            _trace_count_response(item) for item in summary.by_handling_result
        ],
        top_breakdowns=[
            _trace_breakdown_response(item) for item in summary.top_breakdowns
        ],
    )


def _trace_count_response(item: TraceCount) -> TraceCountResponse:
    return TraceCountResponse(key=item.key, count=item.count)


def _trace_breakdown_response(item: TraceBreakdown) -> TraceBreakdownResponse:
    return TraceBreakdownResponse(
        route=item.route,
        category=item.category,
        intent=item.intent,
        handling_result=item.handling_result,
        intent_source=item.intent_source,
        count=item.count,
    )
