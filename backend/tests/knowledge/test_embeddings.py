import asyncio
import json

import httpx
import pytest

from customer_service.knowledge.embeddings import DashScopeEmbeddingClient, EmbeddingError
from customer_service.knowledge.usage import ModelUsageRecord


class RecordingUsageSink:
    def __init__(self) -> None:
        self.records: list[ModelUsageRecord] = []

    async def record(self, usage: ModelUsageRecord) -> None:
        self.records.append(usage)


def test_embed_batches_and_orders_vectors() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        data = [
            {"index": index, "embedding": [float(index), 1.0]}
            for index, _ in enumerate(body["input"])
        ]
        return httpx.Response(
            200,
            json={
                "data": list(reversed(data)),
                "usage": {"input_tokens": len(body["input"])},
            },
        )

    async def run() -> list[list[float]]:
        client = DashScopeEmbeddingClient(
            api_key="test",
            base_url="https://example.com/v1/",
            dimensions=2,
            usage_sink=sink,
            transport=httpx.MockTransport(handler),
        )
        async with client:
            return await client.embed([str(index) for index in range(11)])

    sink = RecordingUsageSink()
    vectors = asyncio.run(run())

    assert len(requests) == 2
    assert len(vectors) == 11
    assert vectors[0] == [0.0, 1.0]
    assert sink.records == [
        ModelUsageRecord(
            provider="dashscope",
            model="text-embedding-v4",
            purpose="embedding",
            prompt_tokens=10,
            completion_tokens=None,
            total_tokens=None,
            estimated_cost_cny=None,
        ),
        ModelUsageRecord(
            provider="dashscope",
            model="text-embedding-v4",
            purpose="embedding",
            prompt_tokens=1,
            completion_tokens=None,
            total_tokens=None,
            estimated_cost_cny=None,
        ),
    ]


def test_embed_rejects_wrong_dimensions() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0]}]},
        )
    )

    async def run() -> None:
        client = DashScopeEmbeddingClient(
            api_key="test",
            base_url="https://example.com/v1/",
            dimensions=2,
            transport=transport,
        )
        async with client:
            with pytest.raises(EmbeddingError, match="维度"):
                await client.embed(["text"])

    asyncio.run(run())


def test_embed_rejects_non_numeric_vector_items() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, "bad"]}]},
        )
    )

    async def run() -> None:
        client = DashScopeEmbeddingClient(
            api_key="test",
            base_url="https://example.com/v1/",
            dimensions=2,
            transport=transport,
        )
        async with client:
            with pytest.raises(EmbeddingError, match="格式"):
                await client.embed(["text"])

    asyncio.run(run())


def test_embed_rejects_non_contiguous_indices() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"data": [{"index": 1, "embedding": [1.0, 2.0]}]},
        )
    )

    async def run() -> None:
        client = DashScopeEmbeddingClient(
            api_key="test",
            base_url="https://example.com/v1/",
            dimensions=2,
            transport=transport,
        )
        async with client:
            with pytest.raises(EmbeddingError, match="索引"):
                await client.embed(["text"])

    asyncio.run(run())
