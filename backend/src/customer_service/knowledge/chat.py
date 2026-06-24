"""DashScope-compatible chat completion client."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Final, Literal, TypedDict, cast

import httpx

from customer_service.knowledge.embeddings import DEFAULT_BASE_URL
from customer_service.knowledge.usage import (
    LoggingModelUsageSink,
    ModelUsageSink,
    build_usage_record,
)

DEFAULT_CHAT_MODEL: Final = "qwen-plus"
DEFAULT_INTENT_MODEL: Final = "qwen-flash"
DASHSCOPE_PROVIDER: Final = "dashscope"


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionError(RuntimeError):
    """The chat completion service returned an invalid or unsuccessful response."""


class DashScopeChatClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_CHAT_MODEL,
        timeout: float = 60.0,
        json_mode: bool = False,
        usage_sink: ModelUsageSink | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key 不能为空")
        if not model:
            raise ValueError("model 不能为空")
        self.model = model
        self._json_mode = json_mode
        self._usage_sink = usage_sink or LoggingModelUsageSink()
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout),
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    async def __aenter__(self) -> DashScopeChatClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        purpose: str = "chat",
    ) -> str:
        if not messages:
            raise ValueError("messages 不能为空")

        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                payload: dict[str, object] = {
                    "model": self.model,
                    "messages": list(messages),
                }
                if self._json_mode:
                    payload["response_format"] = {"type": "json_object"}
                response = await self._client.post("chat/completions", json=payload)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                break
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                if attempt == 2:
                    raise ChatCompletionError("大模型请求失败，已重试 3 次") from exc
                await asyncio.sleep(2**attempt)

        if response is None:
            raise ChatCompletionError("大模型请求没有返回响应")
        if response.is_error:
            raise ChatCompletionError(
                f"大模型请求失败: HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            raw_payload = response.json()
            if not isinstance(raw_payload, dict):
                raise TypeError
            payload = cast(dict[str, object], raw_payload)
            choices = payload["choices"]
            if not isinstance(choices, list):
                raise TypeError
            choice = cast(list[object], choices)[0]
            if not isinstance(choice, dict):
                raise TypeError
            choice_values = cast(dict[str, object], choice)
            message = choice_values["message"]
            if not isinstance(message, dict):
                raise TypeError
            message_values = cast(dict[str, object], message)
            content = message_values["content"]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ChatCompletionError("大模型响应格式错误") from exc
        if not isinstance(content, str) or not content.strip():
            raise ChatCompletionError("大模型响应内容为空")
        await self._usage_sink.record(
            build_usage_record(
                provider=DASHSCOPE_PROVIDER,
                model=self.model,
                purpose=purpose,
                payload=payload,
            )
        )
        return content.strip()
