"""Evaluate intent routing and category classification against labeled cases."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from customer_service.intents.service import (
    IntentCategory,
    IntentRoute,
    IntentService,
    VALID_CATEGORIES,
    VALID_ROUTES,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases)
    correct_routes = 0
    correct_categories = 0
    correct_pairs = 0
    async with DashScopeChatClient(
        api_key=_required_env("DASHSCOPE_API_KEY"),
        base_url=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
        model=os.getenv("DASHSCOPE_INTENT_MODEL", DEFAULT_INTENT_MODEL),
        json_mode=True,
    ) as classifier:
        service = IntentService(classifier)
        for case in cases:
            decision = await service.recognize(case.query)
            route_ok = decision.route == case.expected_route
            category_ok = decision.category == case.expected_category
            correct_routes += route_ok
            correct_categories += category_ok
            correct_pairs += route_ok and category_ok
            print(
                f"[{case.case_id}] route={decision.route} "
                f"category={decision.category} intent={decision.intent} "
                f"confidence={decision.confidence:.2f} "
                f"expected={case.expected_route}/{case.expected_category}"
            )
    total = len(cases)
    print()
    print(f"cases={total}")
    print(f"route_accuracy={correct_routes / total:.3f}")
    print(f"category_accuracy={correct_categories / total:.3f}")
    print(f"pair_accuracy={correct_pairs / total:.3f}")


def load_cases(path: Path) -> list[IntentEvaluationCase]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_cases = payload["cases"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"无法读取意图评估数据: {path}") from exc
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("意图评估 cases 必须是非空列表")

    cases: list[IntentEvaluationCase] = []
    seen_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("意图评估用例必须是对象")
        case_id = raw_case.get("id")
        query = raw_case.get("query")
        route = raw_case.get("expected_route")
        category = raw_case.get("expected_category", raw_case.get("expected_topic"))
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise ValueError(f"意图评估用例 id 无效或重复: {case_id!r}")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"意图评估 query 无效: {case_id}")
        if route not in VALID_ROUTES or category not in VALID_CATEGORIES:
            raise ValueError(f"意图评估标签无效: {case_id}")
        seen_ids.add(case_id)
        cases.append(
            IntentEvaluationCase(
                case_id=case_id,
                query=query.strip(),
                expected_route=cast(IntentRoute, route),
                expected_category=cast(IntentCategory, category),
            )
        )
    return cases


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value
if __name__ == "__main__":
    asyncio.run(run(parse_args()))
