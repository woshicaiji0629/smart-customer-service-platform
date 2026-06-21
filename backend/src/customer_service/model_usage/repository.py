"""PostgreSQL persistence for model usage records."""

from __future__ import annotations

import logging

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    func,
    insert,
    select,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from customer_service.knowledge.usage import ModelUsageRecord


logger = logging.getLogger(__name__)
metadata = MetaData()

model_usage_logs = Table(
    "model_usage_logs",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("provider", String(64), nullable=False, index=True),
    Column("model", String(128), nullable=False, index=True),
    Column("purpose", String(64), nullable=False, index=True),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_tokens", Integer),
    Column("estimated_cost_cny", Numeric(18, 8)),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    ),
)


@dataclass(frozen=True, slots=True)
class ModelUsageSummaryItem:
    provider: str
    model: str
    purpose: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_cny: Decimal


@dataclass(frozen=True, slots=True)
class ModelUsageSummary:
    total: ModelUsageSummaryItem
    items: list[ModelUsageSummaryItem]


class ModelUsageRepository:
    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(database_url)

    async def close(self) -> None:
        await self.engine.dispose()

    async def initialize_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)

    async def record_usage(self, usage: ModelUsageRecord) -> None:
        if not usage.provider:
            raise ValueError("provider 不能为空")
        if not usage.model:
            raise ValueError("model 不能为空")
        if not usage.purpose:
            raise ValueError("purpose 不能为空")

        statement = insert(model_usage_logs).values(
            provider=usage.provider,
            model=usage.model,
            purpose=usage.purpose,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost_cny=usage.estimated_cost_cny,
        )
        async with self.engine.begin() as connection:
            await connection.execute(statement)

    async def summarize_usage(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> ModelUsageSummary:
        if start >= end:
            raise ValueError("start 必须早于 end")

        base_filters = (
            model_usage_logs.c.created_at >= start,
            model_usage_logs.c.created_at < end,
        )
        calls = func.count().label("calls")
        prompt_tokens = func.coalesce(
            func.sum(model_usage_logs.c.prompt_tokens),
            0,
        ).label("prompt_tokens")
        completion_tokens = func.coalesce(
            func.sum(model_usage_logs.c.completion_tokens),
            0,
        ).label("completion_tokens")
        total_tokens = func.coalesce(
            func.sum(model_usage_logs.c.total_tokens),
            0,
        ).label("total_tokens")
        estimated_cost_cny = func.coalesce(
            func.sum(model_usage_logs.c.estimated_cost_cny),
            0,
        ).label("estimated_cost_cny")

        total_statement = select(
            calls,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            estimated_cost_cny,
        ).where(*base_filters)
        item_statement = (
            select(
                model_usage_logs.c.provider,
                model_usage_logs.c.model,
                model_usage_logs.c.purpose,
                calls,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                estimated_cost_cny,
            )
            .where(*base_filters)
            .group_by(
                model_usage_logs.c.provider,
                model_usage_logs.c.model,
                model_usage_logs.c.purpose,
            )
            .order_by(
                model_usage_logs.c.provider,
                model_usage_logs.c.model,
                model_usage_logs.c.purpose,
            )
        )

        async with self.engine.connect() as connection:
            total_row = (await connection.execute(total_statement)).mappings().one()
            item_rows = (await connection.execute(item_statement)).mappings().all()

        return ModelUsageSummary(
            total=ModelUsageSummaryItem(
                provider="",
                model="",
                purpose="total",
                calls=int(total_row["calls"]),
                prompt_tokens=int(total_row["prompt_tokens"]),
                completion_tokens=int(total_row["completion_tokens"]),
                total_tokens=int(total_row["total_tokens"]),
                estimated_cost_cny=_decimal(total_row["estimated_cost_cny"]),
            ),
            items=[_summary_item_from_row(row) for row in item_rows],
        )


class DatabaseModelUsageSink:
    def __init__(self, repository: ModelUsageRepository) -> None:
        self._repository = repository

    async def record(self, usage: ModelUsageRecord) -> None:
        try:
            await self._repository.record_usage(usage)
        except SQLAlchemyError:
            logger.exception("failed_to_record_model_usage")


def _summary_item_from_row(row: Any) -> ModelUsageSummaryItem:
    return ModelUsageSummaryItem(
        provider=row["provider"],
        model=row["model"],
        purpose=row["purpose"],
        calls=int(row["calls"]),
        prompt_tokens=int(row["prompt_tokens"]),
        completion_tokens=int(row["completion_tokens"]),
        total_tokens=int(row["total_tokens"]),
        estimated_cost_cny=_decimal(row["estimated_cost_cny"]),
    )


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
