"""DashScope-compatible embedding client."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Final

import httpx


DEFAULT_BASE_URL: Final = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL: Final = "text-embedding-v4"
DEFAULT_DIMENSIONS: Final = 1_024
MAX_BATCH_SIZE: Final = 10


class EmbeddingError(RuntimeError):
    """The embedding service returned an invalid or unsuccessful response."""


class DashScopeEmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIMENSIONS,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key 不能为空")
        if dimensions <= 0:
            raise ValueError("dimensions 必须大于 0")
        self.model = model
        self.dimensions = dimensions
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def __aenter__(self) -> DashScopeEmbeddingClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH_SIZE):
            vectors.extend(await self._embed_batch(texts[start : start + MAX_BATCH_SIZE]))
        return vectors

    async def _embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                response = await self._client.post(
                    "embeddings",
                    json={
                        "model": self.model,
                        "input": list(texts),
                        "dimensions": self.dimensions,
                    },
                )
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                break
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                if attempt == 2:
                    raise EmbeddingError("Embedding 请求失败，已重试 3 次") from exc
                await asyncio.sleep(2**attempt)

        if response is None:
            raise EmbeddingError("Embedding 请求没有返回响应")
        if response.is_error:
            raise EmbeddingError(
                f"Embedding 请求失败: HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            payload = response.json()
            items = sorted(payload["data"], key=lambda item: item["index"])
            indices = [item["index"] for item in items]
            vectors = [item["embedding"] for item in items]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("Embedding 响应格式错误") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"Embedding 数量不一致: 请求 {len(texts)}，返回 {len(vectors)}"
            )
        if indices != list(range(len(texts))):
            raise EmbeddingError("Embedding 响应索引不连续")
        if any(
            not isinstance(vector, list) or len(vector) != self.dimensions
            for vector in vectors
        ):
            raise EmbeddingError(f"Embedding 维度不是预期的 {self.dimensions}")
        return vectors
