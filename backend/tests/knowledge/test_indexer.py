import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from customer_service.knowledge.documents import KnowledgeChunk, KnowledgeDocument
from customer_service.knowledge.indexer import build_index
from customer_service.knowledge.repository import IndexState


class UnusedRepository:
    async def get_states(self, article_ids: list[str]) -> dict[str, IndexState]:
        raise AssertionError("repository should not be used")

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
        raise AssertionError("repository should not be used")


class UnusedEmbeddingClient:
    model = "text-embedding-v4"
    dimensions = 1024

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("embedding client should not be used")


def test_build_index_rejects_missing_data_directory(tmp_path: Path) -> None:
    async def run() -> None:
        with pytest.raises(FileNotFoundError, match="数据目录不存在"):
            await build_index(
                data_dir=tmp_path / "missing",
                source="test",
                repository=UnusedRepository(),
                embedding_client=UnusedEmbeddingClient(),
            )

    asyncio.run(run())


def test_build_index_rejects_empty_data_directory(tmp_path: Path) -> None:
    async def run() -> None:
        with pytest.raises(ValueError, match="没有 Markdown"):
            await build_index(
                data_dir=tmp_path,
                source="test",
                repository=UnusedRepository(),
                embedding_client=UnusedEmbeddingClient(),
            )

    asyncio.run(run())
