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

    async def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query 不能为空")
        query_vector = (await self._embedding_client.embed([normalized_query]))[0]
        return await self._repository.search(query_vector, limit=limit)
