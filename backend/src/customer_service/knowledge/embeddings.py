"""DashScope-compatible embedding client."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from collections.abc import Mapping
from typing import Final, cast

import httpx

from customer_service.knowledge.usage import (
    LoggingModelUsageSink,
    ModelUsageSink,
    build_usage_record,
)


DEFAULT_BASE_URL: Final = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL: Final = "text-embedding-v4"
DEFAULT_DIMENSIONS: Final = 1_024
MAX_BATCH_SIZE: Final = 10
DASHSCOPE_PROVIDER: Final = "dashscope"


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
        usage_sink: ModelUsageSink | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key 不能为空")
        if dimensions <= 0:
            raise ValueError("dimensions 必须大于 0")
        self.model = model
        self.dimensions = dimensions
        self._usage_sink = usage_sink or LoggingModelUsageSink()
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout),
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
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
            raw_payload = response.json()
            if not isinstance(raw_payload, dict):
                raise TypeError
            payload = cast(dict[str, object], raw_payload)
            data = payload["data"]
            if not isinstance(data, list):
                raise TypeError
            parsed_items: list[tuple[int, list[float]]] = []
            for item in cast(list[object], data):
                if not isinstance(item, Mapping):
                    raise TypeError
                item_values = cast(Mapping[str, object], item)
                index = item_values["index"]
                embedding = item_values["embedding"]
                if isinstance(index, bool) or not isinstance(index, int):
                    raise TypeError
                if not isinstance(embedding, list):
                    raise TypeError
                parsed_items.append(
                    (index, _parse_embedding_vector(cast(list[object], embedding)))
                )
            parsed_items.sort(key=lambda item: item[0])
            indices = [index for index, _ in parsed_items]
            vectors = [vector for _, vector in parsed_items]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("Embedding 响应格式错误") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"Embedding 数量不一致: 请求 {len(texts)}，返回 {len(vectors)}"
            )
        if indices != list(range(len(texts))):
            raise EmbeddingError("Embedding 响应索引不连续")
        if any(
            len(vector) != self.dimensions for vector in vectors
        ):
            raise EmbeddingError(f"Embedding 维度不是预期的 {self.dimensions}")
        await self._usage_sink.record(
            build_usage_record(
                provider=DASHSCOPE_PROVIDER,
                model=self.model,
                purpose="embedding",
                payload=payload,
            )
        )
        return vectors


def _parse_embedding_vector(value: list[object]) -> list[float]:
    vector: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise TypeError
        vector.append(float(item))
    return vector
