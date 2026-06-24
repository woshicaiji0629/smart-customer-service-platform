"""Model usage accounting helpers."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Final, Protocol, cast


logger = logging.getLogger(__name__)

MODEL_TOKEN_PRICES_CNY_PER_1M: Final = {
    ("dashscope", "qwen-flash"): {"input": 0.15, "output": 1.50},
    ("dashscope", "qwen-plus"): {"input": 0.80, "output": 2.00},
}


@dataclass(frozen=True, slots=True)
class ModelUsageRecord:
    provider: str
    model: str
    purpose: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    estimated_cost_cny: float | None


class ModelUsageSink(Protocol):
    async def record(self, usage: ModelUsageRecord) -> None: ...


class LoggingModelUsageSink:
    async def record(self, usage: ModelUsageRecord) -> None:
        logger.info("model_usage", extra={"model_usage": asdict(usage)})


def build_usage_record(
    *,
    provider: str,
    model: str,
    purpose: str,
    payload: dict[str, Any],
) -> ModelUsageRecord:
    raw_usage = payload.get("usage")
    if not isinstance(raw_usage, Mapping):
        return ModelUsageRecord(
            provider=provider,
            model=model,
            purpose=purpose,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            estimated_cost_cny=None,
        )

    usage = cast(Mapping[str, object], raw_usage)
    prompt_tokens = _int_or_none(
        usage.get(
            "prompt_tokens",
            usage.get("input_tokens", usage.get("promptTokenCount")),
        )
    )
    completion_tokens = _int_or_none(
        usage.get(
            "completion_tokens",
            usage.get("output_tokens", usage.get("candidatesTokenCount")),
        )
    )
    total_tokens = _int_or_none(usage.get("total_tokens", usage.get("totalTokenCount")))
    if (
        total_tokens is None
        and prompt_tokens is not None
        and completion_tokens is not None
    ):
        total_tokens = prompt_tokens + completion_tokens

    return ModelUsageRecord(
        provider=provider,
        model=model,
        purpose=purpose,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost_cny=_estimate_cost_cny(
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


def _estimate_cost_cny(
    *,
    provider: str,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> float | None:
    price = MODEL_TOKEN_PRICES_CNY_PER_1M.get((provider, model))
    if price is None or prompt_tokens is None or completion_tokens is None:
        return None
    return round(
        (prompt_tokens * price["input"] + completion_tokens * price["output"])
        / 1_000_000,
        8,
    )


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
