"""Evaluate semantic retrieval against labeled local cases."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from customer_service.knowledge.embeddings import (
    DEFAULT_BASE_URL,
    DEFAULT_DIMENSIONS,
    DEFAULT_MODEL,
    DashScopeEmbeddingClient,
)
from customer_service.knowledge.repository import KnowledgeRepository, SearchResult
from customer_service.knowledge.service import KnowledgeSearchService

DEFAULT_CASES_PATH: Final = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "knowledge_retrieval_cases.json"
)
MINIMUM_LIMIT: Final = 5
DEFAULT_LIMIT: Final = 20


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    kind: Literal["positive", "out_of_domain"]
    query: str
    expected_article_ids: frozenset[str]
    category: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    hit_at_1: float
    hit_at_3: float
    hit_at_5: float
    mrr: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--include-keyword",
        action="store_true",
        help="追加简单关键词召回结果，用于对比混合检索效果。",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    if args.limit < MINIMUM_LIMIT:
        raise ValueError(f"limit 不能小于 {MINIMUM_LIMIT}")
    cases = load_cases(args.cases)
    database_url = _required_env("DATABASE_URL")
    api_key = _required_env("DASHSCOPE_API_KEY")
    repository = KnowledgeRepository(database_url)
    positive_ranks: list[int | None] = []

    try:
        async with DashScopeEmbeddingClient(
            api_key=api_key,
            base_url=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
            model=DEFAULT_MODEL,
            dimensions=DEFAULT_DIMENSIONS,
        ) as embedding_client:
            service = KnowledgeSearchService(
                repository=repository,
                embedding_client=embedding_client,
            )
            for case in cases:
                results = await service.search(
                    case.query,
                    limit=args.limit,
                    category=case.category,
                    include_keyword=args.include_keyword,
                )
                rank = find_expected_rank(results, case.expected_article_ids)
                top_score = results[0].score if results else None
                if case.kind == "positive":
                    positive_ranks.append(rank)
                    matched_score = _matched_score(results, case.expected_article_ids)
                    print(
                        f"[{case.case_id}] rank={rank or '-'} "
                        f"category={case.category or '-'} "
                        f"top_score={_format_score(top_score)} "
                        f"matched_score={_format_score(matched_score)}"
                    )
                else:
                    top_title = results[0].title if results else "-"
                    print(
                        f"[{case.case_id}] out_of_domain "
                        f"category={case.category or '-'} "
                        f"top_score={_format_score(top_score)} top={top_title}"
                    )
    finally:
        await repository.close()

    metrics = calculate_metrics(positive_ranks)
    print()
    print(f"include_keyword={args.include_keyword}")
    print(f"positive_cases={len(positive_ranks)}")
    print(f"Hit@1={metrics.hit_at_1:.3f}")
    print(f"Hit@3={metrics.hit_at_3:.3f}")
    print(f"Hit@5={metrics.hit_at_5:.3f}")
    print(f"MRR={metrics.mrr:.3f}")


def load_cases(path: Path) -> list[EvaluationCase]:
    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw_payload, dict):
            raise TypeError
        payload = cast(dict[str, object], raw_payload)
        raw_cases = payload["cases"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"无法读取评估数据: {path}") from exc
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("评估数据 cases 必须是非空列表")

    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    for raw_case in cast(list[object], raw_cases):
        if not isinstance(raw_case, dict):
            raise ValueError("评估用例必须是对象")
        case_values = cast(dict[str, object], raw_case)
        try:
            case_id = case_values["id"]
            kind = case_values["kind"]
            raw_query = case_values["query"]
            raw_expected_ids = case_values["expected_article_ids"]
        except KeyError as exc:
            raise ValueError("评估用例格式错误") from exc
        raw_category = case_values.get("category")
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise ValueError(f"评估用例 id 无效或重复: {case_id!r}")
        if kind not in ("positive", "out_of_domain"):
            raise ValueError(f"评估用例 kind 无效: {kind!r}")
        if not isinstance(raw_query, str):
            raise ValueError(f"评估用例 query 必须是字符串: {case_id}")
        query = raw_query.strip()
        if not query:
            raise ValueError(f"评估用例 query 不能为空: {case_id}")
        if not isinstance(raw_expected_ids, list) or any(
            not isinstance(article_id, str) or not article_id
            for article_id in cast(list[object], raw_expected_ids)
        ):
            raise ValueError(f"expected_article_ids 必须是字符串数组: {case_id}")
        category = _parse_category(case_id, raw_category)
        expected_ids = frozenset(cast(list[str], raw_expected_ids))
        if kind == "positive" and not expected_ids:
            raise ValueError(f"正样本缺少 expected_article_ids: {case_id}")
        if kind == "out_of_domain" and expected_ids:
            raise ValueError(f"域外样本不能设置 expected_article_ids: {case_id}")
        seen_ids.add(case_id)
        cases.append(
            EvaluationCase(
                case_id=case_id,
                kind=kind,
                query=query,
                expected_article_ids=expected_ids,
                category=category,
            )
        )
    if not any(case.kind == "positive" for case in cases):
        raise ValueError("评估数据至少需要一个正样本")
    return cases


def _parse_category(case_id: str, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"category 必须是字符串: {case_id}")
    category = value.strip()
    if not category:
        raise ValueError(f"category 不能为空: {case_id}")
    return category


def find_expected_rank(
    results: list[SearchResult],
    expected_article_ids: frozenset[str],
) -> int | None:
    seen: set[str] = set()
    article_rank = 0
    for result in results:
        if result.article_id in seen:
            continue
        seen.add(result.article_id)
        article_rank += 1
        if result.article_id in expected_article_ids:
            return article_rank
    return None


def calculate_metrics(ranks: list[int | None]) -> EvaluationMetrics:
    if not ranks:
        raise ValueError("没有可计算指标的正样本")
    total = len(ranks)
    return EvaluationMetrics(
        hit_at_1=sum(rank == 1 for rank in ranks) / total,
        hit_at_3=sum(rank is not None and rank <= 3 for rank in ranks) / total,
        hit_at_5=sum(rank is not None and rank <= 5 for rank in ranks) / total,
        mrr=sum(1 / rank for rank in ranks if rank is not None) / total,
    )


def _matched_score(
    results: list[SearchResult],
    expected_article_ids: frozenset[str],
) -> float | None:
    return next(
        (
            result.score
            for result in results
            if result.article_id in expected_article_ids
        ),
        None,
    )


def _format_score(score: float | None) -> str:
    return "-" if score is None else f"{score:.4f}"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
