"""Mock exchange business data used by the customer-service flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol


WITHDRAWAL_STATUS_QUERY_TERMS: Final = (
    "状态",
    "进度",
    "处理中",
)
WITHDRAWAL_PLATFORM_HOLD_TERMS: Final = (
    "审核",
    "风控",
    "违规",
    "限制",
    "卡住",
    "不放行",
    "不能提现",
    "提不了现",
    "拒绝",
    "被拒",
    "安全限制",
)
WITHDRAWAL_PERSONAL_TERMS: Final = (
    "我的",
    "我这",
    "帮我查",
    "帮我查询",
    "查询",
    "查一下",
    "进度",
    "状态",
)
WITHDRAWAL_KNOWLEDGE_TERMS: Final = (
    "一般",
    "通常",
    "为什么",
    "什么原因",
    "怎么处理",
    "规则",
    "手续费",
)


@dataclass(frozen=True, slots=True)
class WithdrawalRecord:
    order_id: str
    coin: str
    size: str
    status: Literal["pending", "fail", "success"]
    chain: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DepositRecord:
    txid: str
    coin: str
    size: str
    status: Literal["confirming", "success"]
    chain: str
    updated_at: str


class WithdrawalLookup(Protocol):
    def get_withdrawal(
        self,
        user_id: str,
        order_id: str,
    ) -> WithdrawalRecord | None: ...


class DepositLookup(Protocol):
    def get_deposit(
        self,
        user_id: str,
        txid: str,
    ) -> DepositRecord | None: ...


class MockWithdrawalService:
    _records: Final[dict[str, dict[str, WithdrawalRecord]]] = {
        "10001": {
            "WD-10001": WithdrawalRecord(
                order_id="WD-10001",
                coin="USDT",
                size="120.00",
                status="success",
                chain="TRC20",
                updated_at="2026-06-20T10:30:00+08:00",
            )
        },
        "10002": {
            "WD-10002": WithdrawalRecord(
                order_id="WD-10002",
                coin="BTC",
                size="0.015",
                status="pending",
                chain="Bitcoin",
                updated_at="2026-06-20T11:15:00+08:00",
            )
        },
    }

    def get_withdrawal(
        self,
        user_id: str,
        order_id: str,
    ) -> WithdrawalRecord | None:
        return self._records.get(user_id, {}).get(order_id.upper())


def is_withdrawal_tracking_query(content: str) -> bool:
    is_withdrawal = "提现" in content or "提現" in content
    has_platform_hold = any(term in content for term in WITHDRAWAL_PLATFORM_HOLD_TERMS)
    has_status_query = any(term in content for term in WITHDRAWAL_STATUS_QUERY_TERMS)
    has_personal_context = any(term in content for term in WITHDRAWAL_PERSONAL_TERMS)
    return (
        is_withdrawal
        and (has_platform_hold or (has_status_query and has_personal_context))
        and not any(term in content for term in WITHDRAWAL_KNOWLEDGE_TERMS)
    )


class MockDepositService:
    _records: Final[dict[str, dict[str, DepositRecord]]] = {
        "10001": {
            "TX-10001": DepositRecord(
                txid="TX-10001",
                coin="USDT",
                size="88.00",
                status="success",
                chain="TRC20",
                updated_at="2026-06-20T12:30:00+08:00",
            )
        },
        "10002": {
            "TX-10002": DepositRecord(
                txid="TX-10002",
                coin="ETH",
                size="1.2500",
                status="confirming",
                chain="Ethereum",
                updated_at="2026-06-20T12:45:00+08:00",
            )
        },
    }

    def get_deposit(
        self,
        user_id: str,
        txid: str,
    ) -> DepositRecord | None:
        return self._records.get(user_id, {}).get(txid.upper())


MOCK_WITHDRAWAL_SERVICE: Final = MockWithdrawalService()
MOCK_DEPOSIT_SERVICE: Final = MockDepositService()
