"""PostgreSQL storage for knowledge documents and vector chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    desc,
    delete,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from customer_service.knowledge.documents import KnowledgeChunk, KnowledgeDocument


EMBEDDING_DIMENSIONS = 1_024
MAX_SEARCH_LIMIT = 100
KEYWORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*|[\u4e00-\u9fff]{2,}")
metadata = MetaData()

knowledge_documents = Table(
    "knowledge_documents",
    metadata,
    Column("article_id", String, primary_key=True),
    Column("source", String, nullable=False, index=True),
    Column("title", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("section", Text, nullable=False),
    Column("source_url", Text, nullable=False),
    Column("file_path", Text, nullable=False),
    Column("published_at", DateTime(timezone=False)),
    Column("crawled_at", DateTime(timezone=True)),
    Column("content_hash", String(64), nullable=False),
    Column("indexed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

knowledge_chunks = Table(
    "knowledge_chunks",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "article_id",
        String,
        ForeignKey("knowledge_documents.article_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("chunk_index", Integer, nullable=False),
    Column("heading", Text),
    Column("content", Text, nullable=False),
    Column("embedding_text", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("embedding_model", String, nullable=False),
    Column("embedding_dimensions", Integer, nullable=False),
    Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
    UniqueConstraint("article_id", "chunk_index"),
)


@dataclass(frozen=True, slots=True)
class IndexState:
    content_hash: str
    embedding_model: str | None
    embedding_dimensions: int | None


@dataclass(frozen=True, slots=True)
class SearchResult:
    article_id: str
    title: str
    source_url: str
    heading: str | None
    content: str
    score: float


class KnowledgeRepository:
    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(database_url)

    async def close(self) -> None:
        await self.engine.dispose()

    async def initialize_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.run_sync(metadata.create_all)

    async def get_states(self, article_ids: list[str]) -> dict[str, IndexState]:
        if not article_ids:
            return {}
        statement = (
            select(
                knowledge_documents.c.article_id,
                knowledge_documents.c.content_hash,
                func.min(knowledge_chunks.c.embedding_model).label("embedding_model"),
                func.min(knowledge_chunks.c.embedding_dimensions).label(
                    "embedding_dimensions"
                ),
            )
            .outerjoin(
                knowledge_chunks,
                knowledge_chunks.c.article_id == knowledge_documents.c.article_id,
            )
            .where(knowledge_documents.c.article_id.in_(article_ids))
            .group_by(
                knowledge_documents.c.article_id,
                knowledge_documents.c.content_hash,
            )
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings()
            return {
                row["article_id"]: IndexState(
                    content_hash=row["content_hash"],
                    embedding_model=row["embedding_model"],
                    embedding_dimensions=row["embedding_dimensions"],
                )
                for row in rows
            }

    async def replace_document(
        self,
        *,
        source: str,
        document: KnowledgeDocument,
        chunks: list[KnowledgeChunk],
        vectors: list[list[float]],
        embedding_model: str,
        embedding_dimensions: int,
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks 与 vectors 数量不一致")
        if embedding_dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"数据库向量维度固定为 {EMBEDDING_DIMENSIONS}，"
                f"不能写入 {embedding_dimensions} 维向量"
            )
        document_values = {
            "article_id": document.article_id,
            "source": source,
            "title": document.title,
            "category": document.category,
            "section": document.section,
            "source_url": document.source_url,
            "file_path": document.file_path,
            "published_at": document.published_at,
            "crawled_at": document.crawled_at,
            "content_hash": document.content_hash,
            "indexed_at": func.now(),
        }
        upsert = insert(knowledge_documents).values(**document_values)
        upsert = upsert.on_conflict_do_update(
            index_elements=[knowledge_documents.c.article_id],
            set_={key: value for key, value in document_values.items() if key != "article_id"},
        )
        chunk_values = [
            {
                "article_id": document.article_id,
                "chunk_index": chunk.chunk_index,
                "heading": chunk.heading,
                "content": chunk.content,
                "embedding_text": chunk.embedding_text,
                "content_hash": chunk.content_hash,
                "embedding_model": embedding_model,
                "embedding_dimensions": embedding_dimensions,
                "embedding": vector,
            }
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        async with self.engine.begin() as connection:
            await connection.execute(upsert)
            await connection.execute(
                delete(knowledge_chunks).where(
                    knowledge_chunks.c.article_id == document.article_id
                )
            )
            if chunk_values:
                await connection.execute(insert(knowledge_chunks), chunk_values)

    async def search(
        self,
        query_vector: list[float],
        *,
        limit: int = 5,
        category: str | None = None,
    ) -> list[SearchResult]:
        if len(query_vector) != EMBEDDING_DIMENSIONS:
            raise ValueError(f"查询向量维度必须是 {EMBEDDING_DIMENSIONS}")
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        if limit > MAX_SEARCH_LIMIT:
            raise ValueError(f"limit 不能超过 {MAX_SEARCH_LIMIT}")
        distance = knowledge_chunks.c.embedding.cosine_distance(query_vector)
        statement = (
            select(
                knowledge_chunks.c.article_id,
                knowledge_documents.c.title,
                knowledge_documents.c.source_url,
                knowledge_chunks.c.heading,
                knowledge_chunks.c.content,
                (1 - distance).label("score"),
            )
            .join(
                knowledge_documents,
                knowledge_documents.c.article_id == knowledge_chunks.c.article_id,
            )
            .order_by(distance)
            .limit(limit)
        )
        if category is not None:
            statement = statement.where(knowledge_documents.c.category == category)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings()
            return [
                SearchResult(
                    article_id=row["article_id"],
                    title=row["title"],
                    source_url=row["source_url"],
                    heading=row["heading"],
                    content=row["content"],
                    score=float(row["score"]),
                )
                for row in rows
            ]

    async def keyword_search(
        self,
        query: str,
        *,
        limit: int = 5,
        category: str | None = None,
    ) -> list[SearchResult]:
        tokens = _keyword_tokens(query)
        if not tokens:
            return []
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        if limit > MAX_SEARCH_LIMIT:
            raise ValueError(f"limit 不能超过 {MAX_SEARCH_LIMIT}")
        conditions = []
        score_parts = []
        for token in tokens:
            pattern = f"%{token}%"
            title_match = knowledge_documents.c.title.ilike(pattern)
            heading_match = knowledge_chunks.c.heading.ilike(pattern)
            content_match = knowledge_chunks.c.content.ilike(pattern)
            conditions.append(or_(title_match, heading_match, content_match))
            score_parts.extend(
                [
                    func.coalesce(title_match.cast(Integer), 0) * 3,
                    func.coalesce(heading_match.cast(Integer), 0) * 2,
                    func.coalesce(content_match.cast(Integer), 0),
                ]
            )
        score = sum(score_parts)
        statement = (
            select(
                knowledge_chunks.c.article_id,
                knowledge_documents.c.title,
                knowledge_documents.c.source_url,
                knowledge_chunks.c.heading,
                knowledge_chunks.c.content,
                score.label("score"),
            )
            .join(
                knowledge_documents,
                knowledge_documents.c.article_id == knowledge_chunks.c.article_id,
            )
            .where(or_(*conditions))
            .order_by(
                desc(score),
                knowledge_chunks.c.article_id,
                knowledge_chunks.c.chunk_index,
            )
            .limit(limit)
        )
        if category is not None:
            statement = statement.where(knowledge_documents.c.category == category)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings()
            return [
                SearchResult(
                    article_id=row["article_id"],
                    title=row["title"],
                    source_url=row["source_url"],
                    heading=row["heading"],
                    content=row["content"],
                    score=float(row["score"]),
                )
                for row in rows
            ]


def _keyword_tokens(query: str) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for token in KEYWORD_RE.findall(query):
        normalized = token.strip()
        if len(normalized) < 2:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        tokens.append(normalized)
    return tokens
