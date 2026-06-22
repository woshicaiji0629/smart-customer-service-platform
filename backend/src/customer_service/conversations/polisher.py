"""LLM-based conversation answer polishing."""

from __future__ import annotations

import re
from typing import Final

from customer_service.intents.service import IntentDecision
from customer_service.knowledge.chat import ChatMessage, DashScopeChatClient
from customer_service.knowledge.rag import RagAnswer


CITATION_RE: Final = re.compile(r"\[资料\s*(\d+)\]")
POLISH_SYSTEM_PROMPT: Final = """你是交易所客服回答润色助手。
你的任务是把知识库回答改写得更自然、更像客服在直接回复用户。
必须遵守：
1. 只润色表达，不新增事实、原因、时效、链接、入口或处理承诺；
2. 不得删除或改错原回答中的 [资料 N] 引用；
3. 不得改变原回答的业务含义；
4. 不要输出“根据资料”“官方指南整理”等生硬表述；
5. 保持简洁，优先用自然短句和必要的项目符号。
只输出润色后的最终回答，不要解释润色过程。"""


class ConversationAnswerPolisher:
    def __init__(self, chat_client: DashScopeChatClient) -> None:
        self._chat_client = chat_client

    async def polish(
        self,
        *,
        question: str,
        answer: RagAnswer,
        decision: IntentDecision,
    ) -> RagAnswer:
        polished = await self._chat_client.complete(
            _build_polish_messages(
                question=question,
                answer=answer.answer,
                decision=decision,
            ),
            purpose="answer_polish",
        )
        if not _keeps_required_citations(answer.answer, polished):
            return answer
        return RagAnswer(answer=polished, sources=answer.sources)


def _build_polish_messages(
    *,
    question: str,
    answer: str,
    decision: IntentDecision,
) -> list[ChatMessage]:
    return [
        {"role": "system", "content": POLISH_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"用户问题：{question}\n"
                f"业务分类：{decision.category}\n"
                f"意图：{decision.intent}\n\n"
                f"待润色回答：\n{answer}"
            ),
        },
    ]


def _keeps_required_citations(original: str, polished: str) -> bool:
    required = set(CITATION_RE.findall(original))
    if not required:
        return True
    kept = set(CITATION_RE.findall(polished))
    return required <= kept
