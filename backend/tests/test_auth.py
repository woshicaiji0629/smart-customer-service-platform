from fastapi.testclient import TestClient

from customer_service.auth.api import get_session_store
from customer_service.auth.session import AuthenticatedUser
from customer_service.main import app


class FakeSessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, AuthenticatedUser] = {}
        self.deleted: list[str] = []

    async def create(self, user: AuthenticatedUser) -> str:
        session_id = f"session-{user.user_id}"
        self.sessions[session_id] = user
        return session_id

    async def get(self, session_id: str) -> AuthenticatedUser | None:
        return self.sessions.get(session_id)

    async def delete(self, session_id: str) -> None:
        self.deleted.append(session_id)
        self.sessions.pop(session_id, None)


def test_mock_login_me_and_logout() -> None:
    store = FakeSessionStore()
    app.dependency_overrides[get_session_store] = lambda: store
    try:
        with TestClient(app) as client:
            app.state.session_cookie_secure = False
            login_response = client.post(
                "/auth/mock-login",
                json={"user_id": "10001"},
            )
            me_response = client.get("/auth/me")
            logout_response = client.post("/auth/logout")
            logged_out_response = client.get("/auth/me")
    finally:
        app.dependency_overrides.clear()

    assert login_response.status_code == 200
    assert login_response.json() == {
        "user_id": "10001",
        "display_name": "模拟用户 Alice",
    }
    assert "HttpOnly" in login_response.headers["set-cookie"]
    assert me_response.status_code == 200
    assert logout_response.status_code == 204
    assert store.deleted == ["session-10001"]
    assert logged_out_response.status_code == 401


def test_mock_login_rejects_unknown_user() -> None:
    app.dependency_overrides[get_session_store] = FakeSessionStore
    try:
        response = TestClient(app).post(
            "/auth/mock-login",
            json={"user_id": "unknown"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json() == {"detail": "未知的 Mock 用户"}


def test_auth_api_is_unavailable_without_session_store() -> None:
    response = TestClient(app).get("/auth/mock-users")

    assert response.status_code == 503
    assert response.json() == {"detail": "Mock 登录服务未启用"}
