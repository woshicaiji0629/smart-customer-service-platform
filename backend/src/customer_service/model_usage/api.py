"""HTTP API for model usage summaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from customer_service.auth.api import CurrentUserDependency
from customer_service.model_usage.repository import (
    ModelUsageRepository,
    ModelUsageSummary,
    ModelUsageSummaryItem,
)


router = APIRouter(prefix="/model-usage", tags=["model-usage"])


class ModelUsageSummaryItemResponse(BaseModel):
    provider: str
    model: str
    purpose: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_cny: str


class ModelUsageSummaryResponse(BaseModel):
    start_ts: int
    end_ts: int
    total: ModelUsageSummaryItemResponse
    items: list[ModelUsageSummaryItemResponse]


def get_model_usage_repository(request: Request) -> ModelUsageRepository:
    repository: ModelUsageRepository | None = getattr(
        request.app.state,
        "model_usage_repository",
        None,
    )
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="模型用量统计服务未配置",
        )
    return repository


ModelUsageRepositoryDependency = Annotated[
    ModelUsageRepository,
    Depends(get_model_usage_repository),
]


@router.get("/summary", response_model=ModelUsageSummaryResponse)
async def summarize_model_usage(
    repository: ModelUsageRepositoryDependency,
    user: CurrentUserDependency,
    start_ts: Annotated[int | None, Query(ge=0)] = None,
    end_ts: Annotated[int | None, Query(ge=0)] = None,
) -> ModelUsageSummaryResponse:
    del user
    start, end = _time_range_from_query(start_ts=start_ts, end_ts=end_ts)
    if start >= end:
        raise HTTPException(status_code=400, detail="start_ts 必须小于 end_ts")
    try:
        summary = await repository.summarize_usage(start=start, end=end)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="模型用量统计数据库暂时不可用",
        ) from exc
    return _summary_response(summary, start=start, end=end)


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


def _summary_response(
    summary: ModelUsageSummary,
    *,
    start: datetime,
    end: datetime,
) -> ModelUsageSummaryResponse:
    return ModelUsageSummaryResponse(
        start_ts=int(start.timestamp()),
        end_ts=int(end.timestamp()),
        total=_item_response(summary.total),
        items=[_item_response(item) for item in summary.items],
    )


def _item_response(item: ModelUsageSummaryItem) -> ModelUsageSummaryItemResponse:
    return ModelUsageSummaryItemResponse(
        provider=item.provider,
        model=item.model,
        purpose=item.purpose,
        calls=item.calls,
        prompt_tokens=item.prompt_tokens,
        completion_tokens=item.completion_tokens,
        total_tokens=item.total_tokens,
        estimated_cost_cny=_format_decimal(item.estimated_cost_cny),
    )


def _format_decimal(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.00000001")), "f")
