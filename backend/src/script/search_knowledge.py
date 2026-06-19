"""Run an internal semantic search against the PostgreSQL knowledge index."""

from __future__ import annotations

import argparse
import asyncio
import os

from customer_service.knowledge.embeddings import (
    DEFAULT_BASE_URL,
    DEFAULT_DIMENSIONS,
    DEFAULT_MODEL,
    DashScopeEmbeddingClient,
)
from customer_service.knowledge.repository import KnowledgeRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=5)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    database_url = _required_env("DATABASE_URL")
    api_key = _required_env("DASHSCOPE_API_KEY")
    repository = KnowledgeRepository(database_url)
    try:
        async with DashScopeEmbeddingClient(
            api_key=api_key,
            base_url=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
            model=DEFAULT_MODEL,
            dimensions=DEFAULT_DIMENSIONS,
        ) as embedding_client:
            query_vector = (await embedding_client.embed([args.query]))[0]
        results = await repository.search(query_vector, limit=args.limit)
    finally:
        await repository.close()

    for index, result in enumerate(results, start=1):
        heading = f" / {result.heading}" if result.heading else ""
        print(f"[{index}] {result.score:.4f} {result.title}{heading}")
        print(result.source_url)
        print(result.content[:300].replace("\n", " "))
        print()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
