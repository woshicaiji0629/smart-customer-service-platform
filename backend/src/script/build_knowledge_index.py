"""Build the PostgreSQL/pgvector knowledge index from crawled Markdown files."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from customer_service.knowledge.embeddings import (
    DEFAULT_BASE_URL,
    DEFAULT_DIMENSIONS,
    DEFAULT_MODEL,
    DashScopeEmbeddingClient,
)
from customer_service.knowledge.indexer import build_index
from customer_service.knowledge.repository import KnowledgeRepository


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "bitget_support"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--source", default="bitget_support")
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    database_url = _required_env("DATABASE_URL")
    api_key = _required_env("DASHSCOPE_API_KEY")
    base_url = os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL)
    repository = KnowledgeRepository(database_url)
    try:
        await repository.initialize_schema()
        async with DashScopeEmbeddingClient(
            api_key=api_key,
            base_url=base_url,
            model=DEFAULT_MODEL,
            dimensions=DEFAULT_DIMENSIONS,
        ) as embedding_client:
            result = await build_index(
                data_dir=args.data_dir.resolve(),
                source=args.source,
                repository=repository,
                embedding_client=embedding_client,
                limit=args.limit,
            )
    finally:
        await repository.close()
    print(
        f"完成：发现 {result.discovered} 篇，索引 {result.indexed} 篇，"
        f"跳过 {result.skipped} 篇，写入 {result.chunks} 个分块"
    )


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
