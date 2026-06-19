from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from customer_service.conversations.api import router as conversation_router
from customer_service.conversations.repository import ConversationRepository
from customer_service.conversations.service import ConversationService
from customer_service.knowledge.embeddings import (
    DEFAULT_BASE_URL,
    DEFAULT_DIMENSIONS,
    DEFAULT_MODEL,
    DashScopeEmbeddingClient,
    EmbeddingError,
)
from customer_service.knowledge.chat import (
    DEFAULT_CHAT_MODEL,
    ChatCompletionError,
    DashScopeChatClient,
)
from customer_service.knowledge.rag import RagCitationError, RagService
from customer_service.knowledge.repository import MAX_SEARCH_LIMIT, KnowledgeRepository
from customer_service.knowledge.service import KnowledgeSearchService


LOCAL_FRONTEND_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.knowledge_search_service = None
    app.state.rag_service = None
    app.state.conversation_service = None
    database_url = os.getenv("DATABASE_URL")
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not database_url or not api_key:
        yield
        return

    repository = KnowledgeRepository(database_url)
    conversation_repository = ConversationRepository(database_url)
    try:
        base_url = os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL)
        async with (
            DashScopeEmbeddingClient(
                api_key=api_key,
                base_url=base_url,
                model=DEFAULT_MODEL,
                dimensions=DEFAULT_DIMENSIONS,
            ) as embedding_client,
            DashScopeChatClient(
                api_key=api_key,
                base_url=base_url,
                model=os.getenv("DASHSCOPE_CHAT_MODEL", DEFAULT_CHAT_MODEL),
            ) as chat_client,
        ):
            app.state.knowledge_search_service = KnowledgeSearchService(
                repository=repository,
                embedding_client=embedding_client,
            )
            app.state.rag_service = RagService(
                search_service=app.state.knowledge_search_service,
                chat_client=chat_client,
            )
            app.state.conversation_service = ConversationService(
                repository=conversation_repository,
                rag_service=app.state.rag_service,
            )
            yield
    finally:
        app.state.knowledge_search_service = None
        app.state.rag_service = None
        app.state.conversation_service = None
        await conversation_repository.close()
        await repository.close()


app = FastAPI(title="Smart Customer Service API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_FRONTEND_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.include_router(conversation_router)


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


class RagAnswerRequest(BaseModel):
    question: str

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question 不能为空")
        return value


class RagSourceResponse(BaseModel):
    article_id: str
    title: str
    source_url: str


class RagAnswerResponse(BaseModel):
    answer: str
    sources: list[RagSourceResponse]


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


def get_rag_service(request: Request) -> RagService:
    service: RagService | None = getattr(request.app.state, "rag_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG 问答服务未配置",
        )
    return service


RagServiceDependency = Annotated[RagService, Depends(get_rag_service)]


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


@app.post(
    "/knowledge/answer",
    response_model=RagAnswerResponse,
    tags=["knowledge"],
)
async def answer_from_knowledge(
    body: RagAnswerRequest,
    service: RagServiceDependency,
) -> RagAnswerResponse:
    try:
        result = await service.answer(body.question)
    except EmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Embedding 服务请求失败",
        ) from exc
    except ChatCompletionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="大模型服务请求失败",
        ) from exc
    except RagCitationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="大模型引用校验失败",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="知识库暂时不可用",
        ) from exc
    return RagAnswerResponse(
        answer=result.answer,
        sources=[
            RagSourceResponse(
                article_id=source.article_id,
                title=source.title,
                source_url=source.source_url,
            )
            for source in result.sources
        ],
    )
