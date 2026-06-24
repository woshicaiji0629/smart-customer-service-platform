"""Rule-based entity extraction for customer-service messages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

WITHDRAWAL_ORDER_ID_RE: Final = re.compile(r"\bWD-\d+\b", re.IGNORECASE)
DEPOSIT_TXID_RE: Final = re.compile(r"\bTX-\d+\b", re.IGNORECASE)
COIN_RE: Final = re.compile(r"\b(USDT|USDC|BTC|ETH|BGB)\b", re.IGNORECASE)
NETWORK_RE: Final = re.compile(
    r"\b(TRC20|ERC20|BEP20|Bitcoin|Ethereum)\b",
    re.IGNORECASE,
)
DATE_RE: Final = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b")
TIME_RE: Final = re.compile(r"\b\d{1,2}:\d{2}\b")
RELATIVE_TIME_TERMS: Final = ("刚刚", "今天", "昨天", "前天")
NETWORK_NORMALIZATION: Final = {
    "trc20": "TRC20",
    "erc20": "ERC20",
    "bep20": "BEP20",
    "bitcoin": "Bitcoin",
    "ethereum": "Ethereum",
}


@dataclass(frozen=True, slots=True)
class ExtractedEntities:
    order_id: str | None = None
    txid: str | None = None
    coin: str | None = None
    network: str | None = None
    time_hint: str | None = None

    def to_intent_entities(self) -> dict[str, str]:
        entities: dict[str, str] = {}
        if self.order_id is not None:
            entities["order_id"] = self.order_id
        if self.txid is not None:
            entities["txid"] = self.txid
        if self.coin is not None:
            entities["coin"] = self.coin
        if self.network is not None:
            entities["network"] = self.network
        if self.time_hint is not None:
            entities["time_hint"] = self.time_hint
        return entities


def extract_withdrawal_order_id(content: str) -> str | None:
    match = WITHDRAWAL_ORDER_ID_RE.search(content)
    return match.group(0).upper() if match else None


def extract_deposit_txid(content: str) -> str | None:
    match = DEPOSIT_TXID_RE.search(content)
    return match.group(0).upper() if match else None


def extract_coin(content: str) -> str | None:
    match = COIN_RE.search(content)
    return match.group(1).upper() if match else None


def extract_network(content: str) -> str | None:
    match = NETWORK_RE.search(content)
    if match is None:
        return None
    return NETWORK_NORMALIZATION[match.group(1).lower()]


def extract_time_hint(content: str) -> str | None:
    date_match = DATE_RE.search(content)
    time_match = TIME_RE.search(content)
    if date_match is not None and time_match is not None:
        return f"{date_match.group(0)} {time_match.group(0)}"
    if date_match is not None:
        return date_match.group(0)
    for term in RELATIVE_TIME_TERMS:
        if term in content:
            if time_match is not None:
                return f"{term} {time_match.group(0)}"
            return term
    return time_match.group(0) if time_match is not None else None


def extract_entities(content: str) -> ExtractedEntities:
    return ExtractedEntities(
        order_id=extract_withdrawal_order_id(content),
        txid=extract_deposit_txid(content),
        coin=extract_coin(content),
        network=extract_network(content),
        time_hint=extract_time_hint(content),
    )
