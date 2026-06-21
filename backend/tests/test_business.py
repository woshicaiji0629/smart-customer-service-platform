from fastapi.testclient import TestClient

from customer_service.auth.api import get_session_store
from customer_service.auth.session import AuthenticatedUser
from customer_service.business.service import (
    MOCK_DEPOSIT_SERVICE,
    MOCK_WITHDRAWAL_SERVICE,
    extract_deposit_txid,
    extract_withdrawal_order_id,
    is_withdrawal_tracking_query,
)
from customer_service.main import app


class FakeSessionStore:
    async def create(self, user: AuthenticatedUser) -> str:
        return "unused"

    async def get(self, session_id: str) -> AuthenticatedUser | None:
        users = {
            "alice-session": AuthenticatedUser("10001", "模拟用户 Alice"),
            "bob-session": AuthenticatedUser("10002", "模拟用户 Bob"),
        }
        return users.get(session_id)

    async def delete(self, session_id: str) -> None:
        return None


def test_withdrawal_query_returns_current_users_record() -> None:
    app.dependency_overrides[get_session_store] = FakeSessionStore
    try:
        client = TestClient(app)
        client.cookies.set("smart_support_session", "alice-session")
        response = client.get("/business/withdrawals/WD-10001")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["order_id"] == "WD-10001"


def test_withdrawal_query_does_not_expose_another_users_record() -> None:
    app.dependency_overrides[get_session_store] = FakeSessionStore
    try:
        client = TestClient(app)
        client.cookies.set("smart_support_session", "alice-session")
        response = client.get("/business/withdrawals/WD-10002")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "未找到该用户的提现记录"}


def test_withdrawal_query_requires_login() -> None:
    app.dependency_overrides[get_session_store] = FakeSessionStore
    try:
        response = TestClient(app).get("/business/withdrawals/WD-10001")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录"}


def test_deposit_query_returns_current_users_record() -> None:
    app.dependency_overrides[get_session_store] = FakeSessionStore
    try:
        client = TestClient(app)
        client.cookies.set("smart_support_session", "alice-session")
        response = client.get("/business/deposits/TX-10001")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["txid"] == "TX-10001"


def test_deposit_query_does_not_expose_another_users_record() -> None:
    app.dependency_overrides[get_session_store] = FakeSessionStore
    try:
        client = TestClient(app)
        client.cookies.set("smart_support_session", "alice-session")
        response = client.get("/business/deposits/TX-10002")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "未找到该用户的充值记录"}


def test_mock_withdrawal_service_normalizes_order_id_case() -> None:
    record = MOCK_WITHDRAWAL_SERVICE.get_withdrawal("10001", "wd-10001")

    assert record is not None
    assert record.order_id == "WD-10001"


def test_mock_deposit_service_normalizes_txid_case() -> None:
    record = MOCK_DEPOSIT_SERVICE.get_deposit("10001", "tx-10001")

    assert record is not None
    assert record.txid == "TX-10001"


def test_extract_withdrawal_order_id_requires_explicit_mock_id() -> None:
    assert extract_withdrawal_order_id("请查询 wd-10001 的状态") == "WD-10001"
    assert extract_withdrawal_order_id("提现为什么没到账") is None


def test_extract_deposit_txid_requires_explicit_mock_id() -> None:
    assert extract_deposit_txid("帮我查 tx-10001") == "TX-10001"
    assert extract_deposit_txid("链上 hash 是 0xabc") is None


def test_withdrawal_tracking_query_requires_withdrawal_and_status_term() -> None:
    assert is_withdrawal_tracking_query("提现完成但钱包没到账怎么办？") is True
    assert is_withdrawal_tracking_query("帮我查询提现进度") is True
    assert is_withdrawal_tracking_query("我的提现还在处理中") is True
    assert is_withdrawal_tracking_query("提现手续费是多少？") is False
    assert is_withdrawal_tracking_query("提现失败一般是什么原因？") is False
    assert is_withdrawal_tracking_query("帮我转人工处理提现失败的问题") is False
    assert is_withdrawal_tracking_query("充值完成了吗？") is False
