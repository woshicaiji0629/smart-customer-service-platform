"""Model usage accounting helpers."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Final, Protocol


logger = logging.getLogger(__name__)

MODEL_TOKEN_PRICES_CNY_PER_1M: Final = {
    "qwen-flash": {"input": 0.15, "output": 1.50},
    "qwen-plus": {"input": 0.80, "output": 2.00},
}


@dataclass(frozen=True, slots=True)
class ModelUsageRecord:
    model: str
    purpose: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    estimated_cost_cny: float | None


class ModelUsageSink(Protocol):
    def record(self, usage: ModelUsageRecord) -> None: ...


class LoggingModelUsageSink:
    def record(self, usage: ModelUsageRecord) -> None:
        logger.info("model_usage", extra={"model_usage": asdict(usage)})


def build_usage_record(
    *,
    model: str,
    purpose: str,
    payload: dict[str, Any],
) -> ModelUsageRecord:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return ModelUsageRecord(
            model=model,
            purpose=purpose,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            estimated_cost_cny=None,
        )

    prompt_tokens = _int_or_none(
        usage.get("prompt_tokens", usage.get("input_tokens"))
    )
    completion_tokens = _int_or_none(
        usage.get("completion_tokens", usage.get("output_tokens"))
    )
    total_tokens = _int_or_none(usage.get("total_tokens"))
    if (
        total_tokens is None
        and prompt_tokens is not None
        and completion_tokens is not None
    ):
        total_tokens = prompt_tokens + completion_tokens

    return ModelUsageRecord(
        model=model,
        purpose=purpose,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost_cny=_estimate_cost_cny(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


def _estimate_cost_cny(
    *,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> float | None:
    price = MODEL_TOKEN_PRICES_CNY_PER_1M.get(model)
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
