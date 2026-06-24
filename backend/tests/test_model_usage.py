import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from customer_service.knowledge.usage import ModelUsageRecord, build_usage_record
from customer_service.model_usage.repository import (
    DatabaseModelUsageSink,
    ModelUsageRepository,
    ModelUsageSummary,
    ModelUsageSummaryItem,
    model_usage_logs,
)
from customer_service.auth.api import get_session_store
from customer_service.auth.session import AuthenticatedUser
from customer_service.main import app


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
START = datetime(2026, 6, 21, tzinfo=UTC)
END = datetime(2026, 6, 22, tzinfo=UTC)
START_TS = int(START.timestamp())
END_TS = int(END.timestamp())


class FakeSessionStore:
    async def create(self, user: AuthenticatedUser) -> str:
        return "unused"

    async def get(self, session_id: str) -> AuthenticatedUser | None:
        if session_id == "alice-session":
            return AuthenticatedUser("10001", "模拟用户 Alice")
        return None

    async def delete(self, session_id: str) -> None:
        return None


class FakeModelUsageRepository:
    def __init__(self) -> None:
        self.start: datetime | None = None
        self.end: datetime | None = None

    async def summarize_usage(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> ModelUsageSummary:
        self.start = start
        self.end = end
        return ModelUsageSummary(
            total=ModelUsageSummaryItem(
                provider="",
                model="",
                purpose="total",
                calls=2,
                prompt_tokens=120,
                completion_tokens=30,
                total_tokens=150,
                estimated_cost_cny=Decimal("0.00018000"),
            ),
            items=[
                ModelUsageSummaryItem(
                    provider="dashscope",
                    model="qwen-plus",
                    purpose="rag_answer",
                    calls=2,
                    prompt_tokens=120,
                    completion_tokens=30,
                    total_tokens=150,
                    estimated_cost_cny=Decimal("0.00018000"),
                )
            ],
        )


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

    sink = DatabaseModelUsageSink(FailingRepository())

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


def test_model_usage_repository_rejects_invalid_summary_range() -> None:
    repository = ModelUsageRepository("postgresql+asyncpg://unused")
    try:
        with pytest.raises(ValueError, match="start"):
            asyncio.run(repository.summarize_usage(start=END, end=START))
    finally:
        asyncio.run(repository.close())


def test_model_usage_summary_api_requires_login() -> None:
    app.dependency_overrides[get_session_store] = FakeSessionStore
    app.state.model_usage_repository = FakeModelUsageRepository()
    try:
        response = TestClient(app).get(
            "/model-usage/summary",
            params={
                "start_ts": START_TS,
                "end_ts": END_TS,
            },
        )
    finally:
        app.dependency_overrides.clear()
        app.state.model_usage_repository = None

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录"}


def test_model_usage_summary_api_returns_503_when_repository_is_missing() -> None:
    app.dependency_overrides[get_session_store] = FakeSessionStore
    app.state.model_usage_repository = None
    try:
        client = TestClient(app)
        client.cookies.set("smart_support_session", "alice-session")
        response = client.get(
            "/model-usage/summary",
            params={
                "start_ts": START_TS,
                "end_ts": END_TS,
            },
        )
    finally:
        app.dependency_overrides.clear()
        app.state.model_usage_repository = None

    assert response.status_code == 503
    assert response.json() == {"detail": "模型用量统计服务未配置"}


def test_model_usage_summary_api_returns_summary() -> None:
    repository = FakeModelUsageRepository()
    app.dependency_overrides[get_session_store] = FakeSessionStore
    app.state.model_usage_repository = repository
    try:
        client = TestClient(app)
        client.cookies.set("smart_support_session", "alice-session")
        response = client.get(
            "/model-usage/summary",
            params={
                "start_ts": START_TS,
                "end_ts": END_TS,
            },
        )
    finally:
        app.dependency_overrides.clear()
        app.state.model_usage_repository = None

    assert response.status_code == 200
    assert repository.start == START
    assert repository.end == END
    assert response.json() == {
        "start_ts": START_TS,
        "end_ts": END_TS,
        "total": {
            "provider": "",
            "model": "",
            "purpose": "total",
            "calls": 2,
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "estimated_cost_cny": "0.00018000",
        },
        "items": [
            {
                "provider": "dashscope",
                "model": "qwen-plus",
                "purpose": "rag_answer",
                "calls": 2,
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
                "estimated_cost_cny": "0.00018000",
            }
        ],
    }


def test_model_usage_summary_api_rejects_invalid_time_range() -> None:
    app.dependency_overrides[get_session_store] = FakeSessionStore
    app.state.model_usage_repository = FakeModelUsageRepository()
    try:
        client = TestClient(app)
        client.cookies.set("smart_support_session", "alice-session")
        response = client.get(
            "/model-usage/summary",
            params={
                "start_ts": END_TS,
                "end_ts": START_TS,
            },
        )
    finally:
        app.dependency_overrides.clear()
        app.state.model_usage_repository = None

    assert response.status_code == 400
    assert response.json() == {"detail": "start_ts 必须小于 end_ts"}


def test_model_usage_summary_api_rejects_partial_time_range() -> None:
    app.dependency_overrides[get_session_store] = FakeSessionStore
    app.state.model_usage_repository = FakeModelUsageRepository()
    try:
        client = TestClient(app)
        client.cookies.set("smart_support_session", "alice-session")
        response = client.get(
            "/model-usage/summary",
            params={"start_ts": START_TS},
        )
    finally:
        app.dependency_overrides.clear()
        app.state.model_usage_repository = None

    assert response.status_code == 400
    assert response.json() == {"detail": "start_ts 和 end_ts 必须同时提供"}


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
