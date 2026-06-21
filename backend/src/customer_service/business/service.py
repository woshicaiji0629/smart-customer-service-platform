"""Mock exchange business data used by the customer-service flow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal, Protocol


WITHDRAWAL_ORDER_ID_RE: Final = re.compile(r"\bWD-\d+\b", re.IGNORECASE)
WITHDRAWAL_TRACKING_TERMS: Final = (
    "到账",
    "状态",
    "进度",
    "处理中",
    "完成",
    "成功",
    "失败",
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
    "没到账",
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


class WithdrawalLookup(Protocol):
    def get_withdrawal(
        self,
        user_id: str,
        order_id: str,
    ) -> WithdrawalRecord | None: ...


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


def extract_withdrawal_order_id(content: str) -> str | None:
    match = WITHDRAWAL_ORDER_ID_RE.search(content)
    return match.group(0).upper() if match else None


def is_withdrawal_tracking_query(content: str) -> bool:
    return (
        "提现" in content
        and any(term in content for term in WITHDRAWAL_TRACKING_TERMS)
        and any(term in content for term in WITHDRAWAL_PERSONAL_TERMS)
        and not any(term in content for term in WITHDRAWAL_KNOWLEDGE_TERMS)
    )


MOCK_WITHDRAWAL_SERVICE: Final = MockWithdrawalService()
