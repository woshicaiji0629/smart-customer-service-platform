"""Initialize PostgreSQL extensions and application tables."""

from __future__ import annotations

import argparse
import asyncio
import os

from customer_service.conversations.repository import ConversationRepository
from customer_service.knowledge.repository import KnowledgeRepository
from customer_service.model_usage.repository import ModelUsageRepository


async def run(*, reset_conversations: bool = False) -> None:
    database_url = _required_env("DATABASE_URL")
    knowledge_repository = KnowledgeRepository(database_url)
    conversation_repository = ConversationRepository(database_url)
    model_usage_repository = ModelUsageRepository(database_url)
    try:
        await knowledge_repository.initialize_schema()
        await conversation_repository.initialize_schema(reset=reset_conversations)
        await model_usage_repository.initialize_schema()
    finally:
        await model_usage_repository.close()
        await conversation_repository.close()
        await knowledge_repository.close()
    reset_message = "（会话表已重建）" if reset_conversations else ""
    print(f"数据库初始化完成：vector、知识库表、会话表、模型用量表{reset_message}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化应用数据库")
    parser.add_argument(
        "--reset-conversations",
        action="store_true",
        help="删除并重建会话和消息表，现有会话数据会丢失",
    )
    return parser.parse_args()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(run(reset_conversations=args.reset_conversations))
