"""Incremental knowledge index build orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from customer_service.knowledge.documents import (
    KnowledgeDocument,
    chunk_document,
    parse_document,
)
from customer_service.knowledge.embeddings import DashScopeEmbeddingClient
from customer_service.knowledge.repository import IndexState, KnowledgeRepository


@dataclass(frozen=True, slots=True)
class IndexResult:
    discovered: int
    indexed: int
    skipped: int
    chunks: int


async def build_index(
    *,
    data_dir: Path,
    source: str,
    repository: KnowledgeRepository,
    embedding_client: DashScopeEmbeddingClient,
    limit: int | None = None,
) -> IndexResult:
    if not data_dir.is_dir():
        raise FileNotFoundError(f"知识库数据目录不存在: {data_dir}")
    paths = sorted(data_dir.rglob("*.md"))
    if not paths:
        raise ValueError(f"知识库数据目录中没有 Markdown 文件: {data_dir}")
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        paths = paths[:limit]
    documents = [parse_document(path, root=data_dir) for path in paths]
    _ensure_unique_article_ids(documents)

    states = await repository.get_states([document.article_id for document in documents])
    pending = [
        document
        for document in documents
        if not _is_current(
            document,
            states.get(document.article_id),
            embedding_client.model,
            embedding_client.dimensions,
        )
    ]

    indexed_chunks = 0
    for document in pending:
        chunks = chunk_document(document)
        vectors = await embedding_client.embed([chunk.embedding_text for chunk in chunks])
        await repository.replace_document(
            source=source,
            document=document,
            chunks=chunks,
            vectors=vectors,
            embedding_model=embedding_client.model,
            embedding_dimensions=embedding_client.dimensions,
        )
        indexed_chunks += len(chunks)

    return IndexResult(
        discovered=len(documents),
        indexed=len(pending),
        skipped=len(documents) - len(pending),
        chunks=indexed_chunks,
    )


def _ensure_unique_article_ids(documents: list[KnowledgeDocument]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for document in documents:
        if document.article_id in seen:
            duplicates.add(document.article_id)
        seen.add(document.article_id)
    if duplicates:
        values = ", ".join(sorted(duplicates))
        raise ValueError(f"发现重复 article_id: {values}")


def _is_current(
    document: KnowledgeDocument,
    state: IndexState | None,
    model: str,
    dimensions: int,
) -> bool:
    return bool(
        state
        and state.content_hash == document.content_hash
        and state.embedding_model == model
        and state.embedding_dimensions == dimensions
    )
