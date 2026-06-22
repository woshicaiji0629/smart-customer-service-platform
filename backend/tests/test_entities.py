from customer_service.entities.service import (
    extract_coin,
    extract_deposit_txid,
    extract_entities,
    extract_network,
    extract_time_hint,
    extract_withdrawal_order_id,
)


def test_extract_withdrawal_order_id_requires_explicit_mock_id() -> None:
    assert extract_withdrawal_order_id("请查询 wd-10001 的状态") == "WD-10001"
    assert extract_withdrawal_order_id("提现为什么没到账") is None


def test_extract_deposit_txid_requires_explicit_mock_id() -> None:
    assert extract_deposit_txid("帮我查 tx-10001") == "TX-10001"
    assert extract_deposit_txid("链上 hash 是 0xabc") is None


def test_extract_entities_normalizes_supported_identifiers() -> None:
    entities = extract_entities("请查 wd-10001 和 tx-10001，USDT 走 trc20，今天 14:30")

    assert entities.order_id == "WD-10001"
    assert entities.txid == "TX-10001"
    assert entities.coin == "USDT"
    assert entities.network == "TRC20"
    assert entities.time_hint == "今天 14:30"
    assert entities.to_intent_entities() == {
        "order_id": "WD-10001",
        "txid": "TX-10001",
        "coin": "USDT",
        "network": "TRC20",
        "time_hint": "今天 14:30",
    }


def test_extract_entities_does_not_guess_implicit_values() -> None:
    entities = extract_entities("提现为什么没到账，链上 hash 是 0xabc")

    assert entities.order_id is None
    assert entities.txid is None
    assert entities.to_intent_entities() == {}


def test_extract_coin_requires_supported_symbol() -> None:
    assert extract_coin("充值了 usdc") == "USDC"
    assert extract_coin("充值了一些币") is None


def test_extract_network_normalizes_supported_network() -> None:
    assert extract_network("网络是 ethereum") == "Ethereum"
    assert extract_network("走 bep20") == "BEP20"
    assert extract_network("主网不确定") is None


def test_extract_time_hint_uses_explicit_time_text() -> None:
    assert extract_time_hint("昨天 09:15 充值") == "昨天 09:15"
    assert extract_time_hint("2026-06-20 14:30 充值") == "2026-06-20 14:30"
    assert extract_time_hint("刚刚提交") == "刚刚"
    assert extract_time_hint("尽快处理") is None
