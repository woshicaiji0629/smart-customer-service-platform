"""Print conversation intent trace summary for operations review."""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime, timedelta

from customer_service.ops.repository import OpsRepository, TraceCount


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--start-ts", type=int)
    parser.add_argument("--end-ts", type=int)
    parser.add_argument("--limit", type=int, default=20)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    start, end = _time_range_from_args(args)
    database_url = _required_env("DATABASE_URL")
    repository = OpsRepository(database_url)
    try:
        summary = await repository.summarize_conversation_traces(
            start=start,
            end=end,
            limit=args.limit,
        )
    finally:
        await repository.close()

    print(f"range={start.isoformat()}..{end.isoformat()}")
    print(f"total_turns={summary.total_turns}")
    _print_counts("intent_source", summary.by_intent_source)
    _print_counts("route", summary.by_route)
    _print_counts("handling_result", summary.by_handling_result)
    print()
    print("top_breakdowns")
    for item in summary.top_breakdowns:
        print(
            f"- count={item.count} source={item.intent_source} "
            f"route={item.route} category={item.category} "
            f"intent={item.intent} handling={item.handling_result}"
        )


def _time_range_from_args(args: argparse.Namespace) -> tuple[datetime, datetime]:
    if (args.start_ts is None) != (args.end_ts is None):
        raise ValueError("--start-ts 和 --end-ts 必须同时提供")
    if args.start_ts is not None and args.end_ts is not None:
        start = datetime.fromtimestamp(args.start_ts, UTC)
        end = datetime.fromtimestamp(args.end_ts, UTC)
    else:
        if args.hours <= 0:
            raise ValueError("--hours 必须大于 0")
        end = datetime.now(UTC).replace(microsecond=0)
        start = end - timedelta(hours=args.hours)
    if start >= end:
        raise ValueError("开始时间必须早于结束时间")
    if args.limit <= 0:
        raise ValueError("--limit 必须大于 0")
    return start, end


def _print_counts(label: str, items: list[TraceCount]) -> None:
    print()
    print(label)
    for item in items:
        print(f"- {item.key}: {item.count}")


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
