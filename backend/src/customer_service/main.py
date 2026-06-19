from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from customer_service.knowledge.embeddings import (
    DEFAULT_BASE_URL,
    DEFAULT_DIMENSIONS,
    DEFAULT_MODEL,
    DashScopeEmbeddingClient,
    EmbeddingError,
)
from customer_service.knowledge.repository import MAX_SEARCH_LIMIT, KnowledgeRepository
from customer_service.knowledge.service import KnowledgeSearchService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.knowledge_search_service = None
    database_url = os.getenv("DATABASE_URL")
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not database_url or not api_key:
        yield
        return

    repository = KnowledgeRepository(database_url)
    try:
        async with DashScopeEmbeddingClient(
            api_key=api_key,
            base_url=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
            model=DEFAULT_MODEL,
            dimensions=DEFAULT_DIMENSIONS,
        ) as embedding_client:
            app.state.knowledge_search_service = KnowledgeSearchService(
                repository=repository,
                embedding_client=embedding_client,
            )
            yield
    finally:
        app.state.knowledge_search_service = None
        await repository.close()


app = FastAPI(title="Smart Customer Service API", lifespan=lifespan)


class KnowledgeSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=MAX_SEARCH_LIMIT)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query 不能为空")
        return value


class KnowledgeSearchItem(BaseModel):
    article_id: str
    title: str
    source_url: str
    heading: str | None
    content: str
    score: float


class KnowledgeSearchResponse(BaseModel):
    results: list[KnowledgeSearchItem]


def get_knowledge_search_service(request: Request) -> KnowledgeSearchService:
    service: KnowledgeSearchService | None = getattr(
        request.app.state,
        "knowledge_search_service",
        None,
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="知识检索服务未配置",
        )
    return service


KnowledgeSearchServiceDependency = Annotated[
    KnowledgeSearchService,
    Depends(get_knowledge_search_service),
]


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/knowledge/search",
    response_model=KnowledgeSearchResponse,
    tags=["knowledge"],
)
async def search_knowledge(
    body: KnowledgeSearchRequest,
    service: KnowledgeSearchServiceDependency,
) -> KnowledgeSearchResponse:
    try:
        results = await service.search(body.query, limit=body.limit)
    except EmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Embedding 服务请求失败",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="知识库暂时不可用",
        ) from exc
    return KnowledgeSearchResponse(
        results=[
            KnowledgeSearchItem(
                article_id=result.article_id,
                title=result.title,
                source_url=result.source_url,
                heading=result.heading,
                content=result.content,
                score=result.score,
            )
            for result in results
        ]
    )
