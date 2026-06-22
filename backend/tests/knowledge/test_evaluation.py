import argparse
import asyncio
from pathlib import Path

import pytest

from customer_service.knowledge.repository import SearchResult
import script.evaluate_knowledge_search as evaluation
from script.evaluate_knowledge_search import (
    DEFAULT_CASES_PATH,
    calculate_metrics,
    find_expected_rank,
    load_cases,
    run,
)


def _result(article_id: str, score: float) -> SearchResult:
    return SearchResult(
        article_id=article_id,
        title=article_id,
        source_url=f"https://example.com/{article_id}",
        heading=None,
        content="content",
        score=score,
    )


def test_find_expected_rank_uses_article_level_ranking() -> None:
    results = [
        _result("article-1", 0.9),
        _result("article-1", 0.8),
        _result("article-2", 0.7),
    ]

    rank = find_expected_rank(results, frozenset({"article-2"}))

    assert rank == 2


def test_calculate_metrics() -> None:
    metrics = calculate_metrics([1, 2, None, 5])

    assert metrics.hit_at_1 == 0.25
    assert metrics.hit_at_3 == 0.5
    assert metrics.hit_at_5 == 0.75
    assert metrics.mrr == pytest.approx(0.425)


def test_default_evaluation_cases_are_valid() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)

    assert len(cases) == 22
    assert sum(case.kind == "positive" for case in cases) == 19
    assert sum(case.kind == "out_of_domain" for case in cases) == 3
    cases_by_id = {case.case_id: case for case in cases}
    assert cases_by_id["withdrawal_not_received"].category == "充值与提现"
    assert cases_by_id["limit_order_not_filled"].category == "现货交易"
    assert cases_by_id["identity_verification_failed"].category == "身份认证"
    assert cases_by_id["deposit_missing_memo_tag"].expected_article_ids == frozenset(
        {"12560603811939"}
    )
    assert cases_by_id["account_pin_setup"].expected_article_ids == frozenset(
        {"12560603821292", "12560603821291"}
    )
    assert cases_by_id["kyb_faq"].category == "身份认证"
    assert cases_by_id["weather_question"].category is None


def test_load_cases_rejects_invalid_category(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        """
        {
          "cases": [
            {
              "id": "bad_category",
              "kind": "positive",
              "query": "提现未到账",
              "category": 123,
              "expected_article_ids": ["article-1"]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="category 必须是字符串"):
        load_cases(cases_path)


def test_run_passes_include_keyword_to_search_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        """
        {
          "cases": [
            {
              "id": "case-1",
              "kind": "positive",
              "query": "提现未到账",
              "category": "充值与提现",
              "expected_article_ids": ["article-1"]
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    class FakeRepository:
        def __init__(self, database_url: str) -> None:
            self.database_url = database_url

        async def close(self) -> None:
            return None

    class FakeEmbeddingClient:
        def __init__(self, **values: object) -> None:
            self.values = values

        async def __aenter__(self) -> "FakeEmbeddingClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    class FakeSearchService:
        def __init__(self, **values: object) -> None:
            self.values = values

        async def search(
            self,
            query: str,
            *,
            limit: int,
            category: str | None,
            include_keyword: bool,
        ) -> list[SearchResult]:
            calls.append(
                {
                    "query": query,
                    "limit": limit,
                    "category": category,
                    "include_keyword": include_keyword,
                }
            )
            return [_result("article-1", 0.9)]

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://example")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(evaluation, "KnowledgeRepository", FakeRepository)
    monkeypatch.setattr(evaluation, "DashScopeEmbeddingClient", FakeEmbeddingClient)
    monkeypatch.setattr(evaluation, "KnowledgeSearchService", FakeSearchService)

    asyncio.run(
        run(
            argparse.Namespace(
                cases=cases_path,
                limit=5,
                include_keyword=True,
            )
        )
    )

    assert calls == [
        {
            "query": "提现未到账",
            "limit": 5,
            "category": "充值与提现",
            "include_keyword": True,
        }
    ]
