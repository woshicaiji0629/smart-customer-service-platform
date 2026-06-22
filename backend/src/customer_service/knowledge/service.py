"""Application service for semantic knowledge search."""

from customer_service.knowledge.embeddings import DashScopeEmbeddingClient
from customer_service.knowledge.repository import KnowledgeRepository, SearchResult


class KnowledgeSearchService:
    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        embedding_client: DashScopeEmbeddingClient,
    ) -> None:
        self._repository = repository
        self._embedding_client = embedding_client

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        category: str | None = None,
        include_keyword: bool = False,
    ) -> list[SearchResult]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query 不能为空")
        normalized_category = category.strip() if category is not None else None
        if normalized_category == "":
            normalized_category = None
        query_vector = (await self._embedding_client.embed([normalized_query]))[0]
        vector_results = await self._repository.search(
            query_vector,
            limit=limit,
            category=normalized_category,
        )
        if not include_keyword:
            return vector_results
        keyword_results = await self._repository.keyword_search(
            normalized_query,
            limit=limit,
            category=normalized_category,
        )
        return _merge_search_results(vector_results, keyword_results, limit=limit)


def _merge_search_results(
    vector_results: list[SearchResult],
    keyword_results: list[SearchResult],
    *,
    limit: int,
) -> list[SearchResult]:
    merged: list[SearchResult] = []
    seen: set[tuple[str, str | None, str]] = set()
    for result in [*vector_results, *keyword_results]:
        key = (result.article_id, result.heading, result.content)
        if key in seen:
            continue
        seen.add(key)
        merged.append(result)
        if len(merged) >= limit:
            break
    return merged
