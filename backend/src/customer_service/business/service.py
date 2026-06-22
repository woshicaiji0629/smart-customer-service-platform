"""Mock exchange business data used by the customer-service flow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal, Protocol


WITHDRAWAL_ORDER_ID_RE: Final = re.compile(r"\bWD-\d+\b", re.IGNORECASE)
DEPOSIT_TXID_RE: Final = re.compile(r"\bTX-\d+\b", re.IGNORECASE)
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


@dataclass(frozen=True, slots=True)
class DepositRecord:
    txid: str
    coin: str
    size: str
    status: Literal["confirming", "success"]
    chain: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ExtractedEntities:
    order_id: str | None = None
    txid: str | None = None

    def to_intent_entities(self) -> dict[str, str]:
        entities: dict[str, str] = {}
        if self.order_id is not None:
            entities["order_id"] = self.order_id
        if self.txid is not None:
            entities["txid"] = self.txid
        return entities


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


def extract_deposit_txid(content: str) -> str | None:
    match = DEPOSIT_TXID_RE.search(content)
    return match.group(0).upper() if match else None


def extract_entities(content: str) -> ExtractedEntities:
    return ExtractedEntities(
        order_id=extract_withdrawal_order_id(content),
        txid=extract_deposit_txid(content),
    )


MOCK_WITHDRAWAL_SERVICE: Final = MockWithdrawalService()
MOCK_DEPOSIT_SERVICE: Final = MockDepositService()
