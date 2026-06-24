"""Redis-backed sessions for the development authentication flow."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Final, Protocol, cast

from redis.asyncio import Redis


SESSION_COOKIE_NAME: Final = "smart_support_session"
SESSION_KEY_PREFIX: Final = "smart-support:session:"
DEFAULT_SESSION_TTL_SECONDS: Final = 8 * 60 * 60


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    display_name: str


class SessionStore(Protocol):
    async def create(self, user: AuthenticatedUser) -> str: ...

    async def get(self, session_id: str) -> AuthenticatedUser | None: ...

    async def delete(self, session_id: str) -> None: ...


class RedisSessionStore:
    def __init__(
        self,
        redis_url: str,
        *,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ) -> None:
        if not redis_url:
            raise ValueError("redis_url 不能为空")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须大于 0")
        redis_from_url = cast(Callable[..., Redis], getattr(Redis, "from_url"))
        self._redis = redis_from_url(redis_url, decode_responses=True)
        self._ttl_seconds = ttl_seconds

    async def create(self, user: AuthenticatedUser) -> str:
        session_id = secrets.token_urlsafe(32)
        await self._redis.set(
            self._key(session_id),
            json.dumps(asdict(user), ensure_ascii=False),
            ex=self._ttl_seconds,
        )
        return session_id

    async def get(self, session_id: str) -> AuthenticatedUser | None:
        payload = await self._redis.get(self._key(session_id))
        if payload is None:
            return None
        try:
            values = json.loads(payload)
            user_id = values["user_id"]
            display_name = values["display_name"]
        except (json.JSONDecodeError, KeyError, TypeError):
            await self.delete(session_id)
            return None
        if not isinstance(user_id, str) or not isinstance(display_name, str):
            await self.delete(session_id)
            return None
        return AuthenticatedUser(user_id=user_id, display_name=display_name)

    async def delete(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))

    async def close(self) -> None:
        await self._redis.aclose()

    @staticmethod
    def _key(session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}{session_id}"
