import asyncio

from fastapi.testclient import TestClient

from customer_service.knowledge.repository import SearchResult, _keyword_tokens
from customer_service.knowledge.service import KnowledgeSearchService
from customer_service.main import app, get_knowledge_search_service


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts = texts
        return [[0.1, 0.2]]


class FakeRepository:
    def __init__(self) -> None:
        self.query_vector: list[float] = []
        self.limit = 0
        self.category: str | None = None
        self.keyword_query: str | None = None
        self.keyword_limit = 0
        self.keyword_category: str | None = None

    async def search(
        self,
        query_vector: list[float],
        *,
        limit: int = 5,
        category: str | None = None,
    ) -> list[SearchResult]:
        self.query_vector = query_vector
        self.limit = limit
        self.category = category
        return [
            SearchResult(
                article_id="article-1",
                title="提现没有到账",
                source_url="https://example.com/article-1",
                heading="排查步骤",
                content="请先检查提现状态。",
                score=0.75,
            )
        ]

    async def keyword_search(
        self,
        query: str,
        *,
        limit: int = 5,
        category: str | None = None,
    ) -> list[SearchResult]:
        self.keyword_query = query
        self.keyword_limit = limit
        self.keyword_category = category
        return [
            SearchResult(
                article_id="article-1",
                title="提现没有到账",
                source_url="https://example.com/article-1",
                heading="排查步骤",
                content="请先检查提现状态。",
                score=3,
            ),
            SearchResult(
                article_id="article-2",
                title="如何检查 TxID",
                source_url="https://example.com/article-2",
                heading="查询方式",
                content="可以在区块链浏览器中查询 TxID。",
                score=2,
            ),
        ]


def test_search_service_embeds_query_and_searches_repository() -> None:
    embedding_client = FakeEmbeddingClient()
    repository = FakeRepository()
    service = KnowledgeSearchService(
        repository=repository,  # type: ignore[arg-type]
        embedding_client=embedding_client,  # type: ignore[arg-type]
    )

    results = asyncio.run(service.search("  提现未到账  ", limit=3))

    assert embedding_client.texts == ["提现未到账"]
    assert repository.query_vector == [0.1, 0.2]
    assert repository.limit == 3
    assert repository.category is None
    assert repository.keyword_query is None
    assert results[0].article_id == "article-1"


def test_search_service_passes_normalized_category_to_repository() -> None:
    embedding_client = FakeEmbeddingClient()
    repository = FakeRepository()
    service = KnowledgeSearchService(
        repository=repository,  # type: ignore[arg-type]
        embedding_client=embedding_client,  # type: ignore[arg-type]
    )

    asyncio.run(service.search("提现未到账", limit=3, category="  提现  "))

    assert repository.category == "提现"
    assert repository.keyword_category is None


def test_search_service_can_append_keyword_results() -> None:
    embedding_client = FakeEmbeddingClient()
    repository = FakeRepository()
    service = KnowledgeSearchService(
        repository=repository,  # type: ignore[arg-type]
        embedding_client=embedding_client,  # type: ignore[arg-type]
    )

    results = asyncio.run(
        service.search(
            "提现 TxID",
            limit=3,
            category="充值与提现",
            include_keyword=True,
        )
    )

    assert repository.keyword_query == "提现 TxID"
    assert repository.keyword_limit == 3
    assert repository.keyword_category == "充值与提现"
    assert [result.article_id for result in results] == ["article-1", "article-2"]


def test_keyword_tokens_keep_meaningful_terms_and_deduplicate() -> None:
    assert _keyword_tokens("提现 TxID txid A USDT") == ["提现", "TxID", "USDT"]


def test_search_api_returns_results() -> None:
    service = KnowledgeSearchService(
        repository=FakeRepository(),  # type: ignore[arg-type]
        embedding_client=FakeEmbeddingClient(),  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_knowledge_search_service] = lambda: service
    try:
        response = TestClient(app).post(
            "/knowledge/search",
            json={"query": "提现未到账", "limit": 3},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["results"][0] == {
        "article_id": "article-1",
        "title": "提现没有到账",
        "source_url": "https://example.com/article-1",
        "heading": "排查步骤",
        "content": "请先检查提现状态。",
        "score": 0.75,
    }


def test_search_api_rejects_blank_query() -> None:
    service = KnowledgeSearchService(
        repository=FakeRepository(),  # type: ignore[arg-type]
        embedding_client=FakeEmbeddingClient(),  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_knowledge_search_service] = lambda: service
    try:
        response = TestClient(app).post(
            "/knowledge/search",
            json={"query": "   "},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_search_api_returns_503_when_service_is_not_configured() -> None:
    app.state.knowledge_search_service = None

    response = TestClient(app).post(
        "/knowledge/search",
        json={"query": "提现未到账"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "知识检索服务未配置"}
