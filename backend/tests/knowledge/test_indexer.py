import asyncio
from pathlib import Path

import pytest

from customer_service.knowledge.indexer import build_index


def test_build_index_rejects_missing_data_directory(tmp_path: Path) -> None:
    async def run() -> None:
        with pytest.raises(FileNotFoundError, match="数据目录不存在"):
            await build_index(
                data_dir=tmp_path / "missing",
                source="test",
                repository=None,  # type: ignore[arg-type]
                embedding_client=None,  # type: ignore[arg-type]
            )

    asyncio.run(run())


def test_build_index_rejects_empty_data_directory(tmp_path: Path) -> None:
    async def run() -> None:
        with pytest.raises(ValueError, match="没有 Markdown"):
            await build_index(
                data_dir=tmp_path,
                source="test",
                repository=None,  # type: ignore[arg-type]
                embedding_client=None,  # type: ignore[arg-type]
            )

    asyncio.run(run())
