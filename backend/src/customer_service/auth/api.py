"""Development-only mock login API."""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from redis.exceptions import RedisError

from customer_service.auth.session import (
    SESSION_COOKIE_NAME,
    AuthenticatedUser,
    SessionStore,
)

MOCK_USERS: Final = {
    "10001": AuthenticatedUser(user_id="10001", display_name="模拟用户 Alice"),
    "10002": AuthenticatedUser(user_id="10002", display_name="模拟用户 Bob"),
}

router = APIRouter(prefix="/auth", tags=["auth"])


class MockLoginRequest(BaseModel):
    user_id: str


class UserResponse(BaseModel):
    user_id: str
    display_name: str


def get_session_store(request: Request) -> SessionStore:
    store: SessionStore | None = getattr(request.app.state, "session_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mock 登录服务未启用",
        )
    return store


SessionStoreDependency = Annotated[SessionStore, Depends(get_session_store)]
SessionCookie = Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)]


async def get_current_user(
    store: SessionStoreDependency,
    session_id: SessionCookie = None,
) -> AuthenticatedUser:
    if not session_id:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        user = await store.get(session_id)
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Session 服务暂时不可用") from exc
    if user is None:
        raise HTTPException(status_code=401, detail="登录状态已失效")
    return user


CurrentUserDependency = Annotated[AuthenticatedUser, Depends(get_current_user)]


@router.get("/mock-users", response_model=list[UserResponse])
async def list_mock_users(store: SessionStoreDependency) -> list[UserResponse]:
    del store
    return [UserResponse(**user.__dict__) for user in MOCK_USERS.values()]


@router.post("/mock-login", response_model=UserResponse)
async def mock_login(
    body: MockLoginRequest,
    response: Response,
    store: SessionStoreDependency,
    request: Request,
) -> UserResponse:
    user = MOCK_USERS.get(body.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="未知的 Mock 用户")
    try:
        session_id = await store.create(user)
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Session 服务暂时不可用") from exc
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        max_age=request.app.state.session_ttl_seconds,
        httponly=True,
        secure=request.app.state.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return UserResponse(**user.__dict__)


@router.get("/me", response_model=UserResponse)
async def get_me(user: CurrentUserDependency) -> UserResponse:
    return UserResponse(**user.__dict__)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    store: SessionStoreDependency,
    session_id: SessionCookie = None,
) -> None:
    if session_id:
        try:
            await store.delete(session_id)
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Session 服务暂时不可用") from exc
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", samesite="lax")
