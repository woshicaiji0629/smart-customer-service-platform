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
        "withdrawal_pending_switch_to_deposit_txid",
        "deposit_pending_human_request",
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
    assert (
        cases_by_id["deposit_missing_arrival_followup"]
        .turns[0]
        .expected_trace
        .missing_fields
        == ("txid",)
    )
    assert (
        cases_by_id["deposit_missing_arrival_followup"]
        .turns[1]
        .expected_trace
        .entities
        == {"txid": "TX-10002"}
    )
    assert (
        cases_by_id["withdrawal_missing_order_followup"]
        .turns[0]
        .expected_trace
        .missing_fields
        == ("order_id",)
    )
    assert (
        cases_by_id["withdrawal_missing_order_followup"]
        .turns[1]
        .expected_trace
        .entities
        == {"order_id": "WD-10001"}
    )
    assert [
        turn.expected_next_action_state
        for turn in cases_by_id["withdrawal_pending_switch_to_deposit_txid"].turns
    ] == ["awaiting_withdrawal_order_id", None]
    assert [
        turn.expected_next_action_state
        for turn in cases_by_id["deposit_pending_human_request"].turns
    ] == ["awaiting_deposit_txid", "awaiting_problem_description"]


def test_conversation_flow_evaluation_runs_deposit_followup_mainline() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)

    results = asyncio.run(evaluate_cases(cases))

    assert len(results) == 13
    assert all(isinstance(result, ConversationFlowTurnResult) for result in results)
    assert all(result.full_ok for result in results)
    results_by_turn = {
        (result.case_id, result.turn_index): result
        for result in results
    }
    assert (
        results_by_turn[
            ("deposit_missing_arrival_followup", 1)
        ].trace["missing_fields"]
        == ("txid",)
    )
    assert (
        results_by_turn[
            ("deposit_missing_arrival_followup", 2)
        ].trace["entities"]
        == {"txid": "TX-10002"}
    )
    assert (
        results_by_turn[
            ("withdrawal_missing_order_followup", 1)
        ].trace["missing_fields"]
        == ("order_id",)
    )
    assert (
        results_by_turn[
            ("withdrawal_missing_order_followup", 2)
        ].trace["entities"]
        == {"order_id": "WD-10001"}
    )
    assert (
        results_by_turn[
            ("withdrawal_pending_switch_to_deposit_txid", 2)
        ].trace["handling_result"]
        == "business_deposit_found"
    )
    assert (
        results_by_turn[
            ("deposit_pending_human_request", 2)
        ].trace["handling_result"]
        == "human_request"
    )
