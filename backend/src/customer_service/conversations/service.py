"""Conversation application service."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import re
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from customer_service.business.service import (
    DepositLookup,
    DepositRecord,
    MOCK_DEPOSIT_SERVICE,
    WithdrawalLookup,
    WithdrawalRecord,
)
from customer_service.conversations.repository import (
    ConversationCursor,
    ConversationHistory,
    ConversationNotFoundError,
    ConversationPage,
    ConversationRecord,
    ConversationTurn,
    ConversationTurnTraceRecord,
    MessageRecord,
)
from customer_service.knowledge.rag import RagAnswer, RagHistoryMessage
from customer_service.entities.service import extract_entities
from customer_service.intents.service import (
    IntentDecision,
    IntentHistoryMessage,
    IntentRecognizer,
)


MAX_MESSAGE_LENGTH = 4_000
MAX_HISTORY_MESSAGES = 6
MAX_RECENT_TRACES = 3
INACTIVE_CONVERSATION_CLOSE_AFTER_SECONDS = 300
DEFAULT_CONVERSATION_LIST_LIMIT = 50
MAX_CONVERSATION_LIST_LIMIT = 100
MANUAL_FALLBACK_AFTER_ATTEMPTS = 2
WITHDRAWAL_ORDER_ID_PROMPT = (
    "请提供提现订单号，例如 WD-10001，我可以帮你查询处理状态。"
)
DEPOSIT_TXID_PROMPT = "请提供充值 TxID，例如 TX-10001，我可以帮你查询充值处理状态。"
DEPOSIT_FOLLOWUP_FIELDS = ["coin", "chain", "deposit_time", "page_hint"]
DEPOSIT_FOLLOWUP_PROMPT = (
    "如果链上已成功但仍未到账，请继续补充币种、网络、充值时间和页面提示"
)
DEPOSIT_FOLLOWUP_RECEIVED_PROMPT = (
    "已记录你的补充信息。当前还无法自动确认链上状态或入账原因，"
    "我会把这类情况计入人工兜底候选；如还有截图或更完整页面提示，可以继续补充。"
)
UNKNOWN_INTENT_PROMPT = "请补充说明你遇到的具体问题、操作步骤或页面提示。"
UNKNOWN_INTENT_FALLBACK_PROMPT = (
    "我还无法判断具体问题。请从提现、充值、身份认证、账户安全中选择一个方向，"
    "或直接发送订单号/TxID；本次未解决情况已记录用于后续兜底统计。"
)
HUMAN_REQUEST_PROMPT = (
    "已记录你的人工客服诉求。请通过平台 App 或网页端的官方在线客服入口联系人工客服；"
    "也可以补充具体问题、订单号或页面提示，方便继续处理。"
)
HUMAN_REQUEST_FALLBACK_PROMPT = (
    "我已记录你需要人工兜底的诉求。请继续补充具体问题、订单号或页面提示，"
    "我会先尝试自动处理，无法处理的情况会进入兜底统计。"
)
LEGACY_HUMAN_REQUEST_PROMPT = (
    "请先描述需要解决的具体问题，我会优先尝试自动查询或提供处理方案。"
)
OUT_OF_SCOPE_ANSWER = "我目前只能处理交易所账户、交易和平台使用相关的问题。"
WITHDRAWAL_ONCHAIN_TRANSPARENT_ANSWER = (
    "如果平台侧提现订单已完成、已广播或已上链，链上状态通常是透明的。"
    "请通过提现 TxID、区块浏览器、目标地址和网络自行核对链上进度及接收方入账规则。"
    "平台客服主要能确认平台侧是否已经放行；如果订单仍显示审核中、处理中、"
    "风控限制或不放行，请提供提现订单号，我可以帮你查询平台侧状态。"
)
INACTIVE_CONVERSATION_NOTICE = (
    "由于你超过 5 分钟未回复，之前的问题已自动关闭。"
    "我们将按新的问题重新处理。"
)
DIGITS_ONLY_RE = re.compile(r"^\d+$")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IntentAnswer:
    answer: RagAnswer
    handling_result: str
    next_action: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class BusinessTask:
    entity_key: str
    missing_prompt: str
    next_action_type: str
    missing_result: str
    found_result: str
    not_found_result: str


@dataclass(frozen=True, slots=True)
class ConversationTaskContext:
    current_task: str
    current_route: str
    current_category: str
    current_intent: str
    collected_entities: dict[str, str]
    missing_fields: tuple[str, ...]
    last_handling_result: str
    manual_fallback_candidate: bool


class AnswerPolisher(Protocol):
    async def polish(
        self,
        *,
        question: str,
        answer: RagAnswer,
        decision: IntentDecision,
    ) -> RagAnswer: ...


class ConversationRepositoryLike(Protocol):
    async def create_conversation(self, user_id: str) -> ConversationRecord: ...

    async def list_conversations(
        self,
        user_id: str,
        *,
        limit: int,
        cursor: ConversationCursor | None,
    ) -> ConversationPage: ...

    async def conversation_exists(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> bool: ...

    async def save_turn(
        self,
        *,
        conversation_id: UUID,
        user_id: str,
        user_content: str,
        assistant_content: str,
        assistant_sources: list[dict[str, str]],
    ) -> ConversationTurn: ...

    async def record_turn_trace(
        self,
        *,
        conversation_id: UUID,
        user_id: str,
        user_message_id: int | None,
        assistant_message_id: int | None,
        route: str,
        category: str,
        intent: str,
        intent_source: str,
        confidence: float,
        entities: dict[str, str],
        missing_fields: tuple[str, ...],
        handling_result: str,
        is_inactive_reset: bool,
    ) -> None: ...

    async def get_recent_messages(
        self,
        conversation_id: UUID,
        *,
        user_id: str,
        limit: int,
    ) -> list[MessageRecord]: ...

    async def get_recent_turn_traces(
        self,
        conversation_id: UUID,
        *,
        user_id: str,
        limit: int,
    ) -> list[ConversationTurnTraceRecord]: ...

    async def get_history(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> ConversationHistory: ...


class ConversationRagService(Protocol):
    async def answer(
        self,
        question: str,
        *,
        history: Sequence[RagHistoryMessage] = (),
        category: str | None = None,
    ) -> RagAnswer: ...


WITHDRAWAL_STATUS_TASK = BusinessTask(
    entity_key="order_id",
    missing_prompt=WITHDRAWAL_ORDER_ID_PROMPT,
    next_action_type="provide_withdrawal_order_id",
    missing_result="missing_withdrawal_order_id",
    found_result="business_withdrawal_found",
    not_found_result="business_withdrawal_not_found",
)
DEPOSIT_STATUS_TASK = BusinessTask(
    entity_key="txid",
    missing_prompt=DEPOSIT_TXID_PROMPT,
    next_action_type="provide_deposit_txid",
    missing_result="missing_deposit_txid",
    found_result="business_deposit_found",
    not_found_result="business_deposit_not_found",
)


class ConversationService:
    def __init__(
        self,
        *,
        repository: ConversationRepositoryLike,
        rag_service: ConversationRagService | None,
        withdrawal_service: WithdrawalLookup,
        intent_service: IntentRecognizer,
        deposit_service: DepositLookup | None = None,
        answer_polisher: AnswerPolisher | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._rag_service = rag_service
        self._deposit_service = deposit_service or MOCK_DEPOSIT_SERVICE
        self._withdrawal_service = withdrawal_service
        self._intent_service = intent_service
        self._answer_polisher = answer_polisher
        self._now = now or (lambda: datetime.now(UTC))

    async def create_conversation(self, user_id: str) -> ConversationRecord:
        return await self._repository.create_conversation(user_id)

    async def list_conversations(
        self,
        user_id: str,
        *,
        limit: int = DEFAULT_CONVERSATION_LIST_LIMIT,
        cursor: ConversationCursor | None = None,
    ) -> ConversationPage:
        if limit > MAX_CONVERSATION_LIST_LIMIT:
            raise ValueError(f"limit 不能超过 {MAX_CONVERSATION_LIST_LIMIT}")
        return await self._repository.list_conversations(
            user_id,
            limit=limit,
            cursor=cursor,
        )

    async def send_message(
        self,
        user_id: str,
        conversation_id: UUID,
        content: str,
    ) -> ConversationTurn:
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("content 不能为空")
        if len(normalized_content) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"content 不能超过 {MAX_MESSAGE_LENGTH} 个字符")
        if not await self._repository.conversation_exists(
            conversation_id,
            user_id,
        ):
            raise ConversationNotFoundError(str(conversation_id))

        recent_messages = await self._repository.get_recent_messages(
            conversation_id,
            user_id=user_id,
            limit=MAX_HISTORY_MESSAGES,
        )
        is_inactive = _is_inactive_context(recent_messages, now=self._now())
        active_messages = [] if is_inactive else recent_messages
        intent_history = [
            IntentHistoryMessage(role=message.role, content=message.content)
            for message in active_messages
        ]
        decision = await self._intent_service.recognize(
            normalized_content,
            history=intent_history,
        )
        active_traces = (
            []
            if is_inactive
            else await self._repository.get_recent_turn_traces(
                conversation_id,
                user_id=user_id,
                limit=MAX_RECENT_TRACES,
            )
        )
        decision, normalized_content = _apply_pending_intent(
            decision,
            active_messages,
            active_traces,
            normalized_content,
        )
        rag_history = [
            RagHistoryMessage(role=message.role, content=message.content)
            for message in active_messages
        ]
        intent_answer = await self._answer_for_intent(
            user_id,
            normalized_content,
            decision,
            rag_history,
        )
        answer = intent_answer.answer
        if is_inactive:
            answer = RagAnswer(
                answer=f"{INACTIVE_CONVERSATION_NOTICE}\n\n{answer.answer}",
                sources=answer.sources,
            )
        sources = [
            {
                "article_id": source.article_id,
                "title": source.title,
                "source_url": source.source_url,
            }
            for source in answer.sources
        ]
        turn = await self._repository.save_turn(
            conversation_id=conversation_id,
            user_id=user_id,
            user_content=normalized_content,
            assistant_content=answer.answer,
            assistant_sources=sources,
        )
        await self._record_turn_trace(
            conversation_id=conversation_id,
            user_id=user_id,
            turn=turn,
            decision=decision,
            handling_result=intent_answer.handling_result,
            is_inactive_reset=is_inactive,
        )
        return replace(turn, next_action=intent_answer.next_action)

    async def _answer_for_intent(
        self,
        user_id: str,
        content: str,
        decision: IntentDecision,
        history: list[RagHistoryMessage],
    ) -> IntentAnswer:
        if decision.category == "withdrawal" and decision.intent == "onchain_status":
            return IntentAnswer(
                answer=RagAnswer(
                    answer=WITHDRAWAL_ONCHAIN_TRANSPARENT_ANSWER,
                    sources=[],
                ),
                handling_result="withdrawal_onchain_transparent",
            )
        if decision.route == "business_query" and decision.category == "withdrawal":
            order_id = _task_entity(
                decision,
                WITHDRAWAL_STATUS_TASK,
                respect_missing_fields=True,
            )
            if not order_id:
                return _missing_task_answer(WITHDRAWAL_STATUS_TASK)
            withdrawal = self._withdrawal_service.get_withdrawal(user_id, order_id)
            if withdrawal is not None and withdrawal.status == "pending":
                return IntentAnswer(
                    answer=RagAnswer(
                        answer=_withdrawal_answer(order_id, withdrawal),
                        sources=[],
                    ),
                    handling_result="business_withdrawal_pending_review",
                    next_action=_next_action(
                        "provide_withdrawal_review_details",
                        manual_fallback_candidate=True,
                    ),
                )
            return IntentAnswer(
                answer=RagAnswer(
                    answer=_withdrawal_answer(order_id, withdrawal),
                    sources=[],
                ),
                handling_result=_task_result(WITHDRAWAL_STATUS_TASK, withdrawal),
            )
        if decision.category == "deposit" and (
            decision.route == "business_query" or decision.intent == "missing_arrival"
        ):
            txid = _task_entity(
                decision,
                DEPOSIT_STATUS_TASK,
                fallback=extract_entities(content).txid,
            )
            if not txid:
                return _missing_task_answer(DEPOSIT_STATUS_TASK)
            deposit = self._deposit_service.get_deposit(user_id, txid)
            return IntentAnswer(
                answer=RagAnswer(answer=_deposit_answer(txid, deposit), sources=[]),
                handling_result=_task_result(DEPOSIT_STATUS_TASK, deposit),
                next_action=(
                    _next_action("provide_deposit_followup_details")
                    if deposit is None
                    else None
                ),
            )
        if decision.category == "deposit" and decision.intent == "followup_details":
            return IntentAnswer(
                answer=RagAnswer(answer=DEPOSIT_FOLLOWUP_RECEIVED_PROMPT, sources=[]),
                handling_result="deposit_followup_received",
                next_action=_next_action(
                    "clarify_problem",
                    manual_fallback_candidate=True,
                ),
            )
        if decision.route == "out_of_scope":
            return IntentAnswer(
                answer=RagAnswer(answer=OUT_OF_SCOPE_ANSWER, sources=[]),
                handling_result="out_of_scope",
            )
        if decision.route == "human_request":
            previous_human_requests = _count_recent_assistant_replies(
                history,
                HUMAN_REQUEST_PROMPT,
                HUMAN_REQUEST_FALLBACK_PROMPT,
                LEGACY_HUMAN_REQUEST_PROMPT,
            )
            if previous_human_requests >= MANUAL_FALLBACK_AFTER_ATTEMPTS - 1:
                return IntentAnswer(
                    answer=RagAnswer(answer=HUMAN_REQUEST_FALLBACK_PROMPT, sources=[]),
                    handling_result="manual_fallback_candidate",
                    next_action=_next_action(
                        "clarify_problem",
                        manual_fallback_candidate=True,
                    ),
                )
            return IntentAnswer(
                answer=RagAnswer(answer=HUMAN_REQUEST_PROMPT, sources=[]),
                handling_result="human_request",
                next_action=_next_action("clarify_problem"),
            )
        if decision.route == "unknown":
            previous_unknowns = _count_recent_assistant_replies(
                history,
                UNKNOWN_INTENT_PROMPT,
                UNKNOWN_INTENT_FALLBACK_PROMPT,
            )
            if previous_unknowns >= MANUAL_FALLBACK_AFTER_ATTEMPTS - 1:
                return IntentAnswer(
                    answer=RagAnswer(answer=UNKNOWN_INTENT_FALLBACK_PROMPT, sources=[]),
                    handling_result="manual_fallback_candidate",
                    next_action=_next_action(
                        "clarify_problem",
                        manual_fallback_candidate=True,
                    ),
                )
            return IntentAnswer(
                answer=RagAnswer(answer=UNKNOWN_INTENT_PROMPT, sources=[]),
                handling_result="unknown",
                next_action=_next_action("clarify_problem"),
            )
        if self._rag_service is None:
            raise RagUnavailableError
        rag_answer = await self._rag_service.answer(
            content,
            history=history,
            category=_knowledge_category_for_intent(decision),
        )
        return IntentAnswer(
            answer=await self._polish_rag_answer_if_needed(
                question=content,
                answer=rag_answer,
                decision=decision,
            ),
            handling_result="rag_answer",
        )

    async def _polish_rag_answer_if_needed(
        self,
        *,
        question: str,
        answer: RagAnswer,
        decision: IntentDecision,
    ) -> RagAnswer:
        if self._answer_polisher is None:
            return answer
        if (
            decision.category != "identity_verification"
            or decision.intent != "verification_failure"
        ):
            return answer
        try:
            polished = await self._answer_polisher.polish(
                question=question,
                answer=answer,
                decision=decision,
            )
        except Exception:
            logger.exception("failed_to_polish_rag_answer")
            return answer
        if not polished.answer.strip():
            return answer
        return polished

    async def _record_turn_trace(
        self,
        *,
        conversation_id: UUID,
        user_id: str,
        turn: ConversationTurn,
        decision: IntentDecision,
        handling_result: str,
        is_inactive_reset: bool,
    ) -> None:
        try:
            await self._repository.record_turn_trace(
                conversation_id=conversation_id,
                user_id=user_id,
                user_message_id=turn.user_message.message_id,
                assistant_message_id=turn.assistant_message.message_id,
                route=decision.route,
                category=decision.category,
                intent=decision.intent,
                intent_source=decision.source,
                confidence=decision.confidence,
                entities=decision.entities,
                missing_fields=decision.missing_fields,
                handling_result=handling_result,
                is_inactive_reset=is_inactive_reset,
            )
        except SQLAlchemyError:
            logger.exception("failed_to_record_conversation_turn_trace")

    async def get_history(
        self,
        user_id: str,
        conversation_id: UUID,
    ) -> ConversationHistory:
        history = await self._repository.get_history(conversation_id, user_id)
        traces = await self._repository.get_recent_turn_traces(
            conversation_id,
            user_id=user_id,
            limit=1,
        )
        next_action = _history_next_action(history.messages, traces)
        if next_action is None:
            return history
        return replace(history, next_action=next_action)


class RagUnavailableError(RuntimeError):
    """Raised when a conversation requires RAG but no model key is configured."""


def _is_inactive_context(
    history: list[MessageRecord],
    *,
    now: datetime,
) -> bool:
    if not history:
        return False
    latest_message = history[-1]
    return (
        now - latest_message.created_at
    ).total_seconds() > INACTIVE_CONVERSATION_CLOSE_AFTER_SECONDS


def _apply_pending_intent(
    decision: IntentDecision,
    history: list[MessageRecord],
    traces: list[ConversationTurnTraceRecord],
    content: str,
) -> tuple[IntentDecision, str]:
    if decision.route != "unknown":
        return decision, content
    task_context = _task_context_from_traces(
        traces,
    ) or _task_context_from_history(history)
    if task_context is None:
        return decision, content
    if task_context.current_task == "deposit_followup":
        return IntentDecision(
            route="human_request",
            category="deposit",
            intent="followup_details",
            confidence=1.0,
            entities={},
            missing_fields=(),
            source="fallback",
        ), content
    if task_context.current_task == "deposit_status":
        normalized_content = _prefixed_pending_identifier(content, prefix="TX")
        entities = {"txid": normalized_content} if normalized_content != content else {}
        missing_fields: tuple[str, ...] = () if entities else ("txid",)
        return IntentDecision(
            route="business_query",
            category="deposit",
            intent="status_query",
            confidence=1.0,
            entities=entities,
            missing_fields=missing_fields,
            source="fallback",
        ), content
    if task_context.current_task == "withdrawal_status":
        normalized_content = _prefixed_pending_identifier(content, prefix="WD")
        entities = (
            {"order_id": normalized_content} if normalized_content != content else {}
        )
        missing_fields = () if entities else ("order_id",)
        return IntentDecision(
            route="business_query",
            category="withdrawal",
            intent="status_query",
            confidence=1.0,
            entities=entities,
            missing_fields=missing_fields,
            source="fallback",
        ), content
    return decision, content


def _prefixed_pending_identifier(content: str, *, prefix: str) -> str:
    stripped = content.strip()
    if not DIGITS_ONLY_RE.fullmatch(stripped):
        return content
    return f"{prefix}-{stripped}"


def _task_context_from_traces(
    traces: list[ConversationTurnTraceRecord],
) -> ConversationTaskContext | None:
    for trace in reversed(traces):
        if trace.is_inactive_reset:
            return None
        if trace.handling_result == "business_deposit_not_found":
            return _task_context(trace, current_task="deposit_followup")
        if trace.handling_result == "missing_deposit_txid":
            return _task_context(trace, current_task="deposit_status")
        if trace.handling_result == "missing_withdrawal_order_id":
            return _task_context(trace, current_task="withdrawal_status")
        return None
    return None


def _task_context(
    trace: ConversationTurnTraceRecord,
    *,
    current_task: str,
) -> ConversationTaskContext:
    return ConversationTaskContext(
        current_task=current_task,
        current_route=trace.route,
        current_category=trace.category,
        current_intent=trace.intent,
        collected_entities=dict(trace.entities),
        missing_fields=trace.missing_fields,
        last_handling_result=trace.handling_result,
        manual_fallback_candidate=trace.handling_result == "manual_fallback_candidate",
    )


def _task_context_from_history(
    history: list[MessageRecord],
) -> ConversationTaskContext | None:
    for message in reversed(history):
        role = getattr(message, "role", None)
        content = getattr(message, "content", "")
        if role != "assistant" or not isinstance(content, str):
            continue
        if DEPOSIT_FOLLOWUP_PROMPT in content:
            return _legacy_task_context(
                current_task="deposit_followup",
                category="deposit",
                intent="followup_details",
                handling_result="business_deposit_not_found",
            )
        if DEPOSIT_TXID_PROMPT in content:
            return _legacy_task_context(
                current_task="deposit_status",
                category="deposit",
                intent="missing_arrival",
                handling_result="missing_deposit_txid",
                missing_fields=("txid",),
            )
        if WITHDRAWAL_ORDER_ID_PROMPT in content:
            return _legacy_task_context(
                current_task="withdrawal_status",
                category="withdrawal",
                intent="missing_arrival",
                handling_result="missing_withdrawal_order_id",
                missing_fields=("order_id",),
            )
        return None
    return None


def _legacy_task_context(
    *,
    current_task: str,
    category: str,
    intent: str,
    handling_result: str,
    missing_fields: tuple[str, ...] = (),
) -> ConversationTaskContext:
    return ConversationTaskContext(
        current_task=current_task,
        current_route="unknown",
        current_category=category,
        current_intent=intent,
        collected_entities={},
        missing_fields=missing_fields,
        last_handling_result=handling_result,
        manual_fallback_candidate=False,
    )


def _history_next_action(
    messages: list[MessageRecord],
    traces: list[ConversationTurnTraceRecord],
) -> dict[str, object] | None:
    if not _last_message_is_assistant(messages):
        return None
    if not traces:
        return None
    trace = traces[-1]
    if trace.is_inactive_reset:
        return None
    return _next_action_from_trace(trace)


def _last_message_is_assistant(messages: list[MessageRecord]) -> bool:
    return bool(messages) and messages[-1].role == "assistant"


def _next_action_from_trace(
    trace: ConversationTurnTraceRecord,
) -> dict[str, object] | None:
    action_by_result = {
        "missing_withdrawal_order_id": "provide_withdrawal_order_id",
        "business_withdrawal_pending_review": "provide_withdrawal_review_details",
        "missing_deposit_txid": "provide_deposit_txid",
        "business_deposit_not_found": "provide_deposit_followup_details",
        "unknown": "clarify_problem",
        "human_request": "clarify_problem",
    }
    if trace.handling_result == "manual_fallback_candidate":
        return _next_action("clarify_problem", manual_fallback_candidate=True)
    action_type = action_by_result.get(trace.handling_result)
    if action_type is None:
        return None
    return _next_action(
        action_type,
        manual_fallback_candidate=trace.handling_result
        == "business_withdrawal_pending_review",
    )


def _missing_task_answer(task: BusinessTask) -> IntentAnswer:
    return IntentAnswer(
        answer=RagAnswer(answer=task.missing_prompt, sources=[]),
        handling_result=task.missing_result,
        next_action=_next_action(task.next_action_type),
    )


def _task_entity(
    decision: IntentDecision,
    task: BusinessTask,
    *,
    fallback: str | None = None,
    respect_missing_fields: bool = False,
) -> str | None:
    if respect_missing_fields and task.entity_key in decision.missing_fields:
        return None
    return decision.entities.get(task.entity_key) or fallback


def _task_result(task: BusinessTask, record: object | None) -> str:
    return task.found_result if record is not None else task.not_found_result


def _knowledge_category_for_intent(decision: IntentDecision) -> str | None:
    categories = {
        "withdrawal": "充值与提现",
        "deposit": "充值与提现",
        "identity_verification": "身份认证",
        "account_security": "账户与安全",
        "spot_trading": "现货交易",
    }
    return categories.get(decision.category)


def _next_action(
    action_type: str,
    *,
    manual_fallback_candidate: bool = False,
) -> dict[str, object]:
    expected_input_by_type = {
        "provide_withdrawal_order_id": "withdrawal_order_id",
        "provide_withdrawal_review_details": "withdrawal_review_details",
        "provide_deposit_txid": "deposit_txid",
        "provide_deposit_followup_details": "deposit_followup_details",
        "clarify_problem": "problem_description",
    }
    missing_fields_by_type = {
        "provide_withdrawal_order_id": ["order_id"],
        "provide_withdrawal_review_details": ["page_hint"],
        "provide_deposit_txid": ["txid"],
        "provide_deposit_followup_details": DEPOSIT_FOLLOWUP_FIELDS,
        "clarify_problem": ["problem_description"],
    }
    state_by_type = {
        "provide_withdrawal_order_id": "awaiting_withdrawal_order_id",
        "provide_withdrawal_review_details": (
            "manual_fallback_candidate"
            if manual_fallback_candidate
            else "awaiting_withdrawal_review_details"
        ),
        "provide_deposit_txid": "awaiting_deposit_txid",
        "provide_deposit_followup_details": "awaiting_deposit_followup_details",
        "clarify_problem": (
            "manual_fallback_candidate"
            if manual_fallback_candidate
            else "awaiting_problem_description"
        ),
    }
    return {
        "type": action_type,
        "state": state_by_type[action_type],
        "expected_input": expected_input_by_type[action_type],
        "missing_fields": missing_fields_by_type[action_type],
        "manual_fallback_candidate": manual_fallback_candidate,
    }


def _count_recent_assistant_replies(
    history: list[RagHistoryMessage],
    *contents: str,
) -> int:
    content_set = set(contents)
    count = 0
    for message in reversed(history):
        if message.role == "user":
            continue
        if message.content not in content_set:
            break
        count += 1
    return count


def _withdrawal_answer(
    order_id: str,
    withdrawal: WithdrawalRecord | None,
) -> str:
    if withdrawal is None:
        return f"未找到当前用户的提现订单 {order_id}。"
    if withdrawal.status == "pending":
        return (
            f"Mock 查询结果：提现订单 {withdrawal.order_id}，"
            f"状态 {withdrawal.status}，数量 {withdrawal.size} {withdrawal.coin}，"
            f"网络 {withdrawal.chain}，更新时间 {withdrawal.updated_at}。"
            "该订单仍在平台侧处理中，可能涉及审核、风控或合规检查；"
            "具体原因以页面提示或平台审核结果为准。"
        )
    return (
        f"Mock 查询结果：提现订单 {withdrawal.order_id}，"
        f"状态 {withdrawal.status}，数量 {withdrawal.size} {withdrawal.coin}，"
        f"网络 {withdrawal.chain}，更新时间 {withdrawal.updated_at}。"
    )


def _deposit_answer(
    txid: str,
    deposit: DepositRecord | None,
) -> str:
    if deposit is None:
        return (
            f"未找到当前用户的充值记录 {txid}。"
            "请先确认 TxID、充值网络和到账账户是否正确。"
            f"{DEPOSIT_FOLLOWUP_PROMPT}，"
            "我会根据这些信息继续排查。"
        )
    return (
        f"Mock 查询结果：充值 TxID {deposit.txid}，"
        f"状态 {deposit.status}，数量 {deposit.size} {deposit.coin}，"
        f"网络 {deposit.chain}，更新时间 {deposit.updated_at}。"
    )
