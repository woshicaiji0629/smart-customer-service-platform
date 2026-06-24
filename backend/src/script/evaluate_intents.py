"""Evaluate intent routing, category, and intent classification against labels."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from customer_service.intents.service import (
    ALLOWED_ENTITY_KEYS,
    VALID_CATEGORIES,
    VALID_INTENTS,
    VALID_ROUTES,
    IntentCategory,
    IntentDecision,
    IntentName,
    IntentRecognizer,
    IntentRoute,
    IntentService,
)
from customer_service.knowledge.chat import DEFAULT_INTENT_MODEL, DashScopeChatClient
from customer_service.knowledge.embeddings import DEFAULT_BASE_URL

DEFAULT_CASES_PATH: Final = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "intent_classification_cases.json"
)


@dataclass(frozen=True, slots=True)
class IntentEvaluationCase:
    case_id: str
    query: str
    expected_route: IntentRoute
    expected_category: IntentCategory
    expected_intent: IntentName
    expected_entities: dict[str, str] | None = None
    expected_missing_fields: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class IntentEvaluationResult:
    case: IntentEvaluationCase
    decision: IntentDecision

    @property
    def route_ok(self) -> bool:
        return self.decision.route == self.case.expected_route

    @property
    def category_ok(self) -> bool:
        return self.decision.category == self.case.expected_category

    @property
    def intent_ok(self) -> bool:
        return self.decision.intent == self.case.expected_intent

    @property
    def entities_ok(self) -> bool:
        if self.case.expected_entities is None:
            return True
        return self.decision.entities == self.case.expected_entities

    @property
    def missing_fields_ok(self) -> bool:
        if self.case.expected_missing_fields is None:
            return True
        return self.decision.missing_fields == self.case.expected_missing_fields

    @property
    def full_ok(self) -> bool:
        return (
            self.route_ok
            and self.category_ok
            and self.intent_ok
            and self.entities_ok
            and self.missing_fields_ok
        )


@dataclass(frozen=True, slots=True)
class IntentEvaluationSummary:
    total: int
    correct_routes: int
    correct_categories: int
    correct_intents: int
    correct_full: int
    entity_cases: int
    correct_entities: int
    missing_field_cases: int
    correct_missing_fields: int
    source_counts: dict[str, int]
    mismatch_counts: dict[str, int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument(
        "--mode",
        choices=("rules", "model"),
        default="model",
        help="rules 只评估确定性规则；model 使用配置的大模型兜底。",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases)
    if args.mode == "rules":
        service = IntentService(None)
        results = await evaluate_cases(cases, service)
    else:
        async with DashScopeChatClient(
            api_key=_required_env("DASHSCOPE_API_KEY"),
            base_url=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
            model=os.getenv("DASHSCOPE_INTENT_MODEL", DEFAULT_INTENT_MODEL),
            json_mode=True,
        ) as classifier:
            results = await evaluate_cases(cases, IntentService(classifier))
    _print_results(cast(Literal["rules", "model"], args.mode), results)


async def evaluate_cases(
    cases: list[IntentEvaluationCase],
    service: IntentRecognizer,
) -> list[IntentEvaluationResult]:
    results: list[IntentEvaluationResult] = []
    for case in cases:
        decision = await service.recognize(case.query)
        results.append(IntentEvaluationResult(case=case, decision=decision))
    return results


def summarize_results(
    results: list[IntentEvaluationResult],
) -> IntentEvaluationSummary:
    mismatch_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for result in results:
        source = result.decision.source
        source_counts[source] = source_counts.get(source, 0) + 1
        if result.full_ok:
            continue
        mismatch_type = _mismatch_type(result)
        mismatch_counts[mismatch_type] = mismatch_counts.get(mismatch_type, 0) + 1
    return IntentEvaluationSummary(
        total=len(results),
        correct_routes=sum(result.route_ok for result in results),
        correct_categories=sum(result.category_ok for result in results),
        correct_intents=sum(result.intent_ok for result in results),
        correct_full=sum(result.full_ok for result in results),
        entity_cases=sum(result.case.expected_entities is not None for result in results),
        correct_entities=sum(
            result.entities_ok
            for result in results
            if result.case.expected_entities is not None
        ),
        missing_field_cases=sum(
            result.case.expected_missing_fields is not None for result in results
        ),
        correct_missing_fields=sum(
            result.missing_fields_ok
            for result in results
            if result.case.expected_missing_fields is not None
        ),
        source_counts=source_counts,
        mismatch_counts=mismatch_counts,
    )


def _print_results(
    mode: Literal["rules", "model"],
    results: list[IntentEvaluationResult],
) -> None:
    for result in results:
        decision = result.decision
        print(
            f"[{result.case.case_id}] route={decision.route} "
            f"category={decision.category} intent={decision.intent} "
            f"source={decision.source} "
            f"confidence={decision.confidence:.2f} "
            f"entities={json.dumps(decision.entities, ensure_ascii=False)} "
            f"missing_fields="
            f"{json.dumps(list(decision.missing_fields), ensure_ascii=False)} "
            f"expected={result.case.expected_route}/"
            f"{result.case.expected_category}/{result.case.expected_intent} "
            f"mismatch={_mismatch_type(result)}"
        )
    summary = summarize_results(results)
    print()
    print(f"mode={mode}")
    print(f"cases={summary.total}")
    print(f"route_accuracy={_accuracy(summary.correct_routes, summary.total):.3f}")
    print(
        f"category_accuracy={_accuracy(summary.correct_categories, summary.total):.3f}"
    )
    print(f"intent_accuracy={_accuracy(summary.correct_intents, summary.total):.3f}")
    print(f"full_accuracy={_accuracy(summary.correct_full, summary.total):.3f}")
    print(
        f"entity_accuracy="
        f"{_accuracy(summary.correct_entities, summary.entity_cases):.3f}"
    )
    print(
        f"missing_field_accuracy="
        f"{_accuracy(summary.correct_missing_fields, summary.missing_field_cases):.3f}"
    )
    print("source_counts=" + json.dumps(summary.source_counts, ensure_ascii=False))
    if summary.mismatch_counts:
        print(
            "mismatch_counts="
            + json.dumps(summary.mismatch_counts, ensure_ascii=False)
        )


def _mismatch_type(result: IntentEvaluationResult) -> str:
    if result.full_ok:
        return "ok"
    mismatches: list[str] = []
    if not result.route_ok:
        mismatches.append("route")
    if not result.category_ok:
        mismatches.append("category")
    if not result.intent_ok:
        mismatches.append("intent")
    if not result.entities_ok:
        mismatches.append("entities")
    if not result.missing_fields_ok:
        mismatches.append("missing_fields")
    return "+".join(mismatches)


def _accuracy(correct: int, total: int) -> float:
    if total == 0:
        return 0.0
    return correct / total


def load_cases(path: Path) -> list[IntentEvaluationCase]:
    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw_payload, dict):
            raise TypeError
        payload = cast(dict[str, object], raw_payload)
        raw_cases = payload["cases"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"无法读取意图评估数据: {path}") from exc
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("意图评估 cases 必须是非空列表")

    cases: list[IntentEvaluationCase] = []
    seen_ids: set[str] = set()
    for raw_case in cast(list[object], raw_cases):
        if not isinstance(raw_case, dict):
            raise ValueError("意图评估用例必须是对象")
        case_values = cast(dict[str, object], raw_case)
        case_id = case_values.get("id")
        query = case_values.get("query")
        route = case_values.get("expected_route")
        category = case_values.get(
            "expected_category",
            case_values.get("expected_topic"),
        )
        intent = case_values.get("expected_intent")
        expected_entities = case_values.get("expected_entities")
        expected_missing_fields = case_values.get("expected_missing_fields")
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise ValueError(f"意图评估用例 id 无效或重复: {case_id!r}")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"意图评估 query 无效: {case_id}")
        if (
            route not in VALID_ROUTES
            or category not in VALID_CATEGORIES
            or intent not in VALID_INTENTS
        ):
            raise ValueError(f"意图评估标签无效: {case_id}")
        normalized_entities = _parse_expected_entities(
            case_id,
            expected_entities,
        )
        normalized_missing_fields = _parse_expected_missing_fields(
            case_id,
            expected_missing_fields,
        )
        seen_ids.add(case_id)
        cases.append(
            IntentEvaluationCase(
                case_id=case_id,
                query=query.strip(),
                expected_route=cast(IntentRoute, route),
                expected_category=cast(IntentCategory, category),
                expected_intent=cast(IntentName, intent),
                expected_entities=normalized_entities,
                expected_missing_fields=normalized_missing_fields,
            )
        )
    return cases


def _parse_expected_entities(
    case_id: str,
    value: object,
) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"意图评估 expected_entities 无效: {case_id}")
    normalized: dict[str, str] = {}
    for key, entity_value in cast(dict[object, object], value).items():
        if key not in ALLOWED_ENTITY_KEYS or not isinstance(entity_value, str):
            raise ValueError(f"意图评估 expected_entities 无效: {case_id}")
        stripped = entity_value.strip()
        if stripped:
            normalized[str(key)] = stripped
    return normalized


def _parse_expected_missing_fields(
    case_id: str,
    value: object,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"意图评估 expected_missing_fields 无效: {case_id}")
    raw_fields = cast(list[object], value)
    if not all(
        isinstance(field, str) and field in ALLOWED_ENTITY_KEYS
        for field in raw_fields
    ):
        raise ValueError(f"意图评估 expected_missing_fields 无效: {case_id}")
    return tuple(cast(list[str], value))


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
