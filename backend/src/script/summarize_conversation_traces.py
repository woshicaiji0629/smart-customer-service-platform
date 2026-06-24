"""Print conversation intent trace summary for operations review."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

from customer_service.ops.repository import OpsRepository, TraceCount, TraceSample


DEFAULT_SAMPLE_HANDLING_RESULTS = (
    "unknown",
    "manual_fallback_candidate",
    "deposit_followup_received",
    "business_withdrawal_pending_review",
    "withdrawal_onchain_transparent",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--start-ts", type=int)
    parser.add_argument("--end-ts", type=int)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--samples",
        action="store_true",
        help="同时导出 unknown/manual fallback 用户原文样本候选。",
    )
    parser.add_argument(
        "--sample-format",
        choices=("candidate", "intent-case-draft"),
        default="candidate",
        help="样本输出格式：candidate 为观测信息，intent-case-draft 为评估用例草稿。",
    )
    parser.add_argument("--sample-limit", type=int, default=20)
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
        samples = (
            await repository.list_conversation_trace_samples(
                start=start,
                end=end,
                handling_results=DEFAULT_SAMPLE_HANDLING_RESULTS,
                limit=args.sample_limit,
            )
            if args.samples
            else []
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
    if args.samples:
        print()
        print("sample_candidates")
        for sample in samples:
            print(
                json.dumps(
                    _sample_output(sample, args.sample_format),
                    ensure_ascii=False,
                )
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
    if args.sample_limit <= 0:
        raise ValueError("--sample-limit 必须大于 0")
    return start, end


def _print_counts(label: str, items: list[TraceCount]) -> None:
    print()
    print(label)
    for item in items:
        print(f"- {item.key}: {item.count}")


def _sample_candidate(sample: TraceSample) -> dict[str, object]:
    return {
        "query": sample.user_content,
        "observed_route": sample.route,
        "observed_category": sample.category,
        "observed_intent": sample.intent,
        "observed_handling_result": sample.handling_result,
        "intent_source": sample.intent_source,
        "confidence": sample.confidence,
        "entities": sample.entities,
        "missing_fields": sample.missing_fields,
        "created_at": sample.created_at.isoformat(),
    }


def _sample_output(sample: TraceSample, output_format: str) -> dict[str, object]:
    if output_format == "candidate":
        return _sample_candidate(sample)
    if output_format == "intent-case-draft":
        return _intent_case_draft(sample)
    raise ValueError(f"未知样本输出格式: {output_format}")


time_range_from_args = _time_range_from_args
sample_candidate = _sample_candidate
sample_output = _sample_output


def _intent_case_draft(sample: TraceSample) -> dict[str, object]:
    return {
        "id": _draft_case_id(sample),
        "query": sample.user_content,
        "expected_route": sample.route,
        "expected_category": sample.category,
        "expected_intent": sample.intent,
        "expected_entities": sample.entities,
        "expected_missing_fields": sample.missing_fields,
        "_review": {
            "observed_handling_result": sample.handling_result,
            "intent_source": sample.intent_source,
            "confidence": sample.confidence,
            "created_at": sample.created_at.isoformat(),
        },
    }


def _draft_case_id(sample: TraceSample) -> str:
    timestamp = sample.created_at.strftime("%Y%m%d%H%M%S")
    digest = hashlib.sha1(sample.user_content.encode("utf-8")).hexdigest()[:8]
    return f"trace_{sample.handling_result}_{timestamp}_{digest}"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
