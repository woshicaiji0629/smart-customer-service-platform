"""Initialize PostgreSQL extensions and application tables."""

from __future__ import annotations

import asyncio
import os

from customer_service.conversations.repository import ConversationRepository
from customer_service.knowledge.repository import KnowledgeRepository


async def run() -> None:
    database_url = _required_env("DATABASE_URL")
    knowledge_repository = KnowledgeRepository(database_url)
    conversation_repository = ConversationRepository(database_url)
    try:
        await knowledge_repository.initialize_schema()
        await conversation_repository.initialize_schema()
    finally:
        await conversation_repository.close()
        await knowledge_repository.close()
    print("数据库初始化完成：vector、知识库表、会话表")


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


if __name__ == "__main__":
    asyncio.run(run())
