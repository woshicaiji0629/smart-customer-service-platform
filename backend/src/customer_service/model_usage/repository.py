"""PostgreSQL persistence for model usage records."""

from __future__ import annotations

import logging

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


class DatabaseModelUsageSink:
    def __init__(self, repository: ModelUsageRepository) -> None:
        self._repository = repository

    async def record(self, usage: ModelUsageRecord) -> None:
        try:
            await self._repository.record_usage(usage)
        except SQLAlchemyError:
            logger.exception("failed_to_record_model_usage")
