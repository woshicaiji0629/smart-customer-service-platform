import pytest

from customer_service.knowledge.repository import SearchResult
from script.evaluate_knowledge_search import (
    DEFAULT_CASES_PATH,
    calculate_metrics,
    find_expected_rank,
    load_cases,
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

    assert len(cases) == 15
    assert sum(case.kind == "positive" for case in cases) == 12
    assert sum(case.kind == "out_of_domain" for case in cases) == 3
