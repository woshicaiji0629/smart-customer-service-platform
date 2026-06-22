"""Evaluate multi-turn conversation flows against expected states and traces."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import UUID

from customer_service.business.service import MOCK_WITHDRAWAL_SERVICE
from customer_service.conversations.repository import (
    ConversationCursor,
    ConversationHistory,
    ConversationNotFoundError,
    ConversationPage,
    ConversationRecord,
    ConversationSummary,
    ConversationTurn,
    MessageRecord,
)
from customer_service.conversations.service import ConversationService
from customer_service.intents.service import IntentService


DEFAULT_CASES_PATH: Final = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "conversation_flow_cases.json"
)
DEFAULT_USER_ID: Final = "10001"
CONVERSATION_ID: Final = UUID("11111111-1111-1111-1111-111111111111")
CREATED_AT: Final = datetime(2026, 6, 19, 8, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ExpectedTrace:
    route: str
    category: str
    intent: str
    handling_result: str


@dataclass(frozen=True, slots=True)
class ConversationFlowTurnCase:
    user: str
    expected_assistant_contains: str
    expected_next_action_state: str | None
    expected_trace: ExpectedTrace


@dataclass(frozen=True, slots=True)
class ConversationFlowCase:
    case_id: str
    user_id: str
    turns: tuple[ConversationFlowTurnCase, ...]


@dataclass(frozen=True, slots=True)
class ConversationFlowTurnResult:
    case_id: str
    turn_index: int
    assistant_content: str
    next_action_state: str | None
    trace: dict[str, object]
    expected: ConversationFlowTurnCase

    @property
    def assistant_ok(self) -> bool:
        return self.expected.expected_assistant_contains in self.assistant_content

    @property
    def next_action_ok(self) -> bool:
        return self.next_action_state == self.expected.expected_next_action_state

    @property
    def trace_ok(self) -> bool:
        expected = self.expected.expected_trace
        return (
            self.trace.get("route") == expected.route
            and self.trace.get("category") == expected.category
            and self.trace.get("intent") == expected.intent
            and self.trace.get("handling_result") == expected.handling_result
        )

    @property
    def full_ok(self) -> bool:
        return self.assistant_ok and self.next_action_ok and self.trace_ok


class MemoryConversationRepository:
    def __init__(self, *, conversation_id: UUID = CONVERSATION_ID) -> None:
        self.conversation_id = conversation_id
        self.messages: list[MessageRecord] = []
        self.traces: list[dict[str, object]] = []
        self._next_message_id = 1

    async def create_conversation(self, user_id: str) -> ConversationRecord:
        return _conversation_record(user_id, self.conversation_id)

    async def conversation_exists(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> bool:
        return conversation_id == self.conversation_id and bool(user_id)

    async def list_conversations(
        self,
        user_id: str,
        *,
        limit: int,
        cursor: ConversationCursor | None,
    ) -> ConversationPage:
        return ConversationPage(
            items=[
                ConversationSummary(
                    conversation=_conversation_record(user_id, self.conversation_id),
                    title="conversation-flow-evaluation",
                )
            ][:limit],
            next_cursor=cursor,
        )

    async def save_turn(
        self,
        *,
        conversation_id: UUID,
        user_id: str,
        user_content: str,
        assistant_content: str,
        assistant_sources: list[dict[str, str]],
    ) -> ConversationTurn:
        if conversation_id != self.conversation_id:
            raise ConversationNotFoundError(str(conversation_id))
        user_message = self._message(
            conversation_id=conversation_id,
            role="user",
            content=user_content,
            sources=[],
        )
        assistant_message = self._message(
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_content,
            sources=assistant_sources,
        )
        self.messages.extend([user_message, assistant_message])
        return ConversationTurn(
            user_message=user_message,
            assistant_message=assistant_message,
        )

    async def record_turn_trace(self, **values: object) -> None:
        self.traces.append(values)

    async def get_recent_messages(
        self,
        conversation_id: UUID,
        *,
        user_id: str,
        limit: int,
    ) -> list[MessageRecord]:
        if conversation_id != self.conversation_id:
            raise ConversationNotFoundError(str(conversation_id))
        return self.messages[-limit:]

    async def get_history(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> ConversationHistory:
        if conversation_id != self.conversation_id:
            raise ConversationNotFoundError(str(conversation_id))
        return ConversationHistory(
            conversation=_conversation_record(user_id, conversation_id),
            messages=list(self.messages),
        )

    def _message(
        self,
        *,
        conversation_id: UUID,
        role: str,
        content: str,
        sources: list[dict[str, str]],
    ) -> MessageRecord:
        message = MessageRecord(
            message_id=self._next_message_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            sources=sources,
            created_at=CREATED_AT,
        )
        self._next_message_id += 1
        return message


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases)
    results = await evaluate_cases(cases)
    _print_results(results)


async def evaluate_cases(
    cases: list[ConversationFlowCase],
) -> list[ConversationFlowTurnResult]:
    results: list[ConversationFlowTurnResult] = []
    for case in cases:
        repository = MemoryConversationRepository()
        service = ConversationService(
            repository=repository,  # type: ignore[arg-type]
            rag_service=None,
            withdrawal_service=MOCK_WITHDRAWAL_SERVICE,
            intent_service=IntentService(None),
            now=lambda: CREATED_AT,
        )
        await service.create_conversation(case.user_id)
        for index, turn_case in enumerate(case.turns, start=1):
            turn = await service.send_message(
                case.user_id,
                repository.conversation_id,
                turn_case.user,
            )
            trace = repository.traces[-1]
            next_action = turn.next_action or {}
            results.append(
                ConversationFlowTurnResult(
                    case_id=case.case_id,
                    turn_index=index,
                    assistant_content=turn.assistant_message.content,
                    next_action_state=_next_action_state(next_action),
                    trace=trace,
                    expected=turn_case,
                )
            )
    return results


def load_cases(path: Path) -> list[ConversationFlowCase]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_cases = payload["cases"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"无法读取会话流评估数据: {path}") from exc
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("会话流评估 cases 必须是非空列表")

    cases: list[ConversationFlowCase] = []
    seen_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("会话流评估用例必须是对象")
        case_id = raw_case.get("id")
        user_id = raw_case.get("user_id", DEFAULT_USER_ID)
        raw_turns = raw_case.get("turns")
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise ValueError(f"会话流评估用例 id 无效或重复: {case_id!r}")
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError(f"会话流评估 user_id 无效: {case_id}")
        if not isinstance(raw_turns, list) or not raw_turns:
            raise ValueError(f"会话流评估 turns 无效: {case_id}")
        seen_ids.add(case_id)
        cases.append(
            ConversationFlowCase(
                case_id=case_id,
                user_id=user_id.strip(),
                turns=tuple(_parse_turn(case_id, raw_turn) for raw_turn in raw_turns),
            )
        )
    return cases


def _parse_turn(case_id: str, raw_turn: object) -> ConversationFlowTurnCase:
    if not isinstance(raw_turn, dict):
        raise ValueError(f"会话流评估 turn 无效: {case_id}")
    user = raw_turn.get("user")
    expected_assistant_contains = raw_turn.get("expected_assistant_contains")
    expected_next_action_state = raw_turn.get("expected_next_action_state")
    raw_trace = raw_turn.get("expected_trace")
    if not isinstance(user, str) or not user.strip():
        raise ValueError(f"会话流评估 user 无效: {case_id}")
    if (
        not isinstance(expected_assistant_contains, str)
        or not expected_assistant_contains
    ):
        raise ValueError(f"会话流评估 expected_assistant_contains 无效: {case_id}")
    if expected_next_action_state is not None and not isinstance(
        expected_next_action_state,
        str,
    ):
        raise ValueError(f"会话流评估 expected_next_action_state 无效: {case_id}")
    if not isinstance(raw_trace, dict):
        raise ValueError(f"会话流评估 expected_trace 无效: {case_id}")
    return ConversationFlowTurnCase(
        user=user.strip(),
        expected_assistant_contains=expected_assistant_contains,
        expected_next_action_state=expected_next_action_state,
        expected_trace=_parse_expected_trace(case_id, raw_trace),
    )


def _parse_expected_trace(case_id: str, raw_trace: dict[object, object]) -> ExpectedTrace:
    values = {
        key: raw_trace.get(key)
        for key in ("route", "category", "intent", "handling_result")
    }
    if not all(isinstance(value, str) and value for value in values.values()):
        raise ValueError(f"会话流评估 expected_trace 无效: {case_id}")
    return ExpectedTrace(
        route=str(values["route"]),
        category=str(values["category"]),
        intent=str(values["intent"]),
        handling_result=str(values["handling_result"]),
    )


def _next_action_state(next_action: dict[str, object]) -> str | None:
    state = next_action.get("state")
    return state if isinstance(state, str) else None


def _print_results(results: list[ConversationFlowTurnResult]) -> None:
    for result in results:
        print(
            f"[{result.case_id}#{result.turn_index}] "
            f"assistant={result.assistant_ok} "
            f"next_action={result.next_action_ok} "
            f"trace={result.trace_ok}"
        )
    total = len(results)
    passed = sum(result.full_ok for result in results)
    print()
    print(f"turns={total}")
    print(f"passed={passed}")
    print(f"full_accuracy={_accuracy(passed, total):.3f}")


def _accuracy(correct: int, total: int) -> float:
    if total == 0:
        return 0.0
    return correct / total


def _conversation_record(user_id: str, conversation_id: UUID) -> ConversationRecord:
    return ConversationRecord(
        conversation_id=conversation_id,
        user_id=user_id,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
