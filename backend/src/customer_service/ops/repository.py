"""PostgreSQL read models for operations metrics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Column, desc, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from customer_service.conversations.repository import conversation_turn_traces, messages


@dataclass(frozen=True, slots=True)
class TraceCount:
    key: str
    count: int


@dataclass(frozen=True, slots=True)
class TraceBreakdown:
    route: str
    category: str
    intent: str
    handling_result: str
    intent_source: str
    count: int


@dataclass(frozen=True, slots=True)
class TraceSummary:
    total_turns: int
    by_intent_source: list[TraceCount]
    by_route: list[TraceCount]
    by_handling_result: list[TraceCount]
    top_breakdowns: list[TraceBreakdown]


@dataclass(frozen=True, slots=True)
class TraceSample:
    user_content: str
    route: str
    category: str
    intent: str
    intent_source: str
    confidence: float
    entities: dict[str, str]
    missing_fields: list[str]
    handling_result: str
    created_at: datetime


class OpsRepository:
    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(database_url)

    async def close(self) -> None:
        await self.engine.dispose()

    async def summarize_conversation_traces(
        self,
        *,
        start: datetime,
        end: datetime,
        limit: int = 20,
    ) -> TraceSummary:
        if start >= end:
            raise ValueError("start 必须早于 end")
        if limit <= 0:
            raise ValueError("limit 必须大于 0")

        filters = (
            conversation_turn_traces.c.created_at >= start,
            conversation_turn_traces.c.created_at < end,
        )
        total_statement = select(func.count()).where(*filters)
        source_statement = _count_by_statement(
            conversation_turn_traces.c.intent_source,
            filters,
        )
        route_statement = _count_by_statement(
            conversation_turn_traces.c.route,
            filters,
        )
        handling_statement = _count_by_statement(
            conversation_turn_traces.c.handling_result,
            filters,
        )
        breakdown_count = func.count().label("count")
        breakdown_statement = (
            select(
                conversation_turn_traces.c.route,
                conversation_turn_traces.c.category,
                conversation_turn_traces.c.intent,
                conversation_turn_traces.c.handling_result,
                conversation_turn_traces.c.intent_source,
                breakdown_count,
            )
            .where(*filters)
            .group_by(
                conversation_turn_traces.c.route,
                conversation_turn_traces.c.category,
                conversation_turn_traces.c.intent,
                conversation_turn_traces.c.handling_result,
                conversation_turn_traces.c.intent_source,
            )
            .order_by(desc(breakdown_count))
            .limit(limit)
        )

        async with self.engine.connect() as connection:
            total = int((await connection.execute(total_statement)).scalar_one())
            source_rows = (
                await connection.execute(source_statement)
            ).mappings().all()
            route_rows = (await connection.execute(route_statement)).mappings().all()
            handling_rows = (
                await connection.execute(handling_statement)
            ).mappings().all()
            breakdown_rows = (
                await connection.execute(breakdown_statement)
            ).mappings().all()

        return TraceSummary(
            total_turns=total,
            by_intent_source=[_trace_count_from_row(row) for row in source_rows],
            by_route=[_trace_count_from_row(row) for row in route_rows],
            by_handling_result=[
                _trace_count_from_row(row) for row in handling_rows
            ],
            top_breakdowns=[
                TraceBreakdown(
                    route=row["route"],
                    category=row["category"],
                    intent=row["intent"],
                    handling_result=row["handling_result"],
                    intent_source=row["intent_source"],
                    count=int(row["count"]),
                )
                for row in breakdown_rows
            ],
        )

    async def list_conversation_trace_samples(
        self,
        *,
        start: datetime,
        end: datetime,
        handling_results: tuple[str, ...],
        limit: int = 20,
    ) -> list[TraceSample]:
        if start >= end:
            raise ValueError("start 必须早于 end")
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        if not handling_results:
            raise ValueError("handling_results 不能为空")

        statement = (
            select(
                messages.c.content.label("user_content"),
                conversation_turn_traces.c.route,
                conversation_turn_traces.c.category,
                conversation_turn_traces.c.intent,
                conversation_turn_traces.c.intent_source,
                conversation_turn_traces.c.confidence,
                conversation_turn_traces.c.entities,
                conversation_turn_traces.c.missing_fields,
                conversation_turn_traces.c.handling_result,
                conversation_turn_traces.c.created_at,
            )
            .join(
                messages,
                messages.c.id == conversation_turn_traces.c.user_message_id,
            )
            .where(
                conversation_turn_traces.c.created_at >= start,
                conversation_turn_traces.c.created_at < end,
                conversation_turn_traces.c.handling_result.in_(handling_results),
            )
            .order_by(conversation_turn_traces.c.created_at.desc())
            .limit(limit)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [
            TraceSample(
                user_content=row["user_content"],
                route=row["route"],
                category=row["category"],
                intent=row["intent"],
                intent_source=row["intent_source"],
                confidence=float(row["confidence"]),
                entities=dict(row["entities"]),
                missing_fields=list(row["missing_fields"]),
                handling_result=row["handling_result"],
                created_at=row["created_at"],
            )
            for row in rows
        ]


def _count_by_statement(column: Column[Any], filters: tuple[Any, ...]) -> Any:
    count = func.count().label("count")
    return (
        select(column.label("key"), count)
        .where(*filters)
        .group_by(column)
        .order_by(desc(count), column)
    )


def _trace_count_from_row(row: Mapping[Any, Any]) -> TraceCount:
    return TraceCount(key=row["key"], count=int(row["count"]))
