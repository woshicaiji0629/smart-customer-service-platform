import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from customer_service.knowledge.usage import ModelUsageRecord, build_usage_record
from customer_service.model_usage.repository import (
    DatabaseModelUsageSink,
    ModelUsageRepository,
    model_usage_logs,
)


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def test_model_usage_schema_has_provider_aware_columns() -> None:
    assert set(model_usage_logs.c.keys()) == {
        "id",
        "provider",
        "model",
        "purpose",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated_cost_cny",
        "created_at",
    }


def test_build_usage_record_supports_provider_specific_usage_fields() -> None:
    record = build_usage_record(
        provider="gemini",
        model="gemini-example",
        purpose="intent",
        payload={
            "usage": {
                "promptTokenCount": 12,
                "candidatesTokenCount": 8,
                "totalTokenCount": 20,
            }
        },
    )

    assert record == ModelUsageRecord(
        provider="gemini",
        model="gemini-example",
        purpose="intent",
        prompt_tokens=12,
        completion_tokens=8,
        total_tokens=20,
        estimated_cost_cny=None,
    )


def test_database_model_usage_sink_does_not_raise_on_database_error() -> None:
    class FailingRepository:
        async def record_usage(self, usage: ModelUsageRecord) -> None:
            raise SQLAlchemyError("database unavailable")

    sink = DatabaseModelUsageSink(FailingRepository())  # type: ignore[arg-type]

    asyncio.run(
        sink.record(
            ModelUsageRecord(
                provider="dashscope",
                model="qwen-plus",
                purpose="rag_answer",
                prompt_tokens=1,
                completion_tokens=2,
                total_tokens=3,
                estimated_cost_cny=0.00001,
            )
        )
    )


@pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="需要通过 TEST_DATABASE_URL 显式启用 PostgreSQL 集成测试",
)
def test_model_usage_repository_records_usage_against_postgresql() -> None:
    asyncio.run(_test_model_usage_repository_records_usage_against_postgresql())


async def _test_model_usage_repository_records_usage_against_postgresql() -> None:
    assert TEST_DATABASE_URL is not None
    schema_name = f"test_model_usage_{uuid4().hex}"
    admin_engine = create_async_engine(TEST_DATABASE_URL)
    repository = None
    schema_created = False

    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        schema_created = True

        repository = ModelUsageRepository(TEST_DATABASE_URL)
        await repository.engine.dispose()
        repository.engine = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        await repository.initialize_schema()
        await repository.record_usage(
            ModelUsageRecord(
                provider="dashscope",
                model="qwen-plus",
                purpose="rag_answer",
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                estimated_cost_cny=0.00012,
            )
        )

        async with repository.engine.connect() as connection:
            row = (
                await connection.execute(select(model_usage_logs))
            ).mappings().one()

        assert row["provider"] == "dashscope"
        assert row["model"] == "qwen-plus"
        assert row["purpose"] == "rag_answer"
        assert row["prompt_tokens"] == 100
        assert row["completion_tokens"] == 20
        assert row["total_tokens"] == 120
        assert row["estimated_cost_cny"] == Decimal("0.00012000")
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
