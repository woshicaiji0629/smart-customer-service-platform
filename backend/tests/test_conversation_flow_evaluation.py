import asyncio

from script.evaluate_conversation_flows import (
    DEFAULT_CASES_PATH,
    ConversationFlowTurnResult,
    evaluate_cases,
    load_cases,
)


def test_conversation_flow_cases_cover_deposit_followup_mainline() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)
    cases_by_id = {case.case_id: case for case in cases}

    assert set(cases_by_id) == {
        "deposit_missing_arrival_followup",
        "withdrawal_missing_order_followup",
        "repeated_unknown_manual_fallback",
        "repeated_human_request_manual_fallback",
    }
    assert [
        turn.expected_next_action_state
        for turn in cases_by_id["deposit_missing_arrival_followup"].turns
    ] == [
        "awaiting_deposit_txid",
        "awaiting_deposit_followup_details",
        "manual_fallback_candidate",
    ]
    assert [
        turn.expected_next_action_state
        for turn in cases_by_id["withdrawal_missing_order_followup"].turns
    ] == ["awaiting_withdrawal_order_id", None]


def test_conversation_flow_evaluation_runs_deposit_followup_mainline() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)

    results = asyncio.run(evaluate_cases(cases))

    assert len(results) == 9
    assert all(isinstance(result, ConversationFlowTurnResult) for result in results)
    assert all(result.full_ok for result in results)
    assert [result.trace["handling_result"] for result in results[:3]] == [
        "missing_deposit_txid",
        "business_deposit_not_found",
        "deposit_followup_received",
    ]
    assert [result.trace["handling_result"] for result in results[3:]] == [
        "missing_withdrawal_order_id",
        "business_withdrawal_found",
        "unknown",
        "manual_fallback_candidate",
        "human_request",
        "manual_fallback_candidate",
    ]
