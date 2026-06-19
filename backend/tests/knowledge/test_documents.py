from pathlib import Path

import pytest

from customer_service.knowledge.documents import (
    DocumentParseError,
    KnowledgeDocument,
    chunk_document,
    parse_document,
)


def test_parse_document_and_clean_html(tmp_path: Path) -> None:
    path = tmp_path / "article.md"
    path.write_text(
        """---
article_id: "123"
source_url: "https://example.com/123"
category: "充值与提现"
section: "问题排除"
published_at: "2025-01-14 06:37"
crawled_at: "2026-06-19T03:38:37+00:00"
---

# 为什么提现没有到账？

## 检查状态

请查看 <strong>提现历史</strong>，然后访问[帮助中心](https://example.com)。
""",
        encoding="utf-8",
    )

    document = parse_document(path, root=tmp_path)

    assert document.article_id == "123"
    assert document.title == "为什么提现没有到账？"
    assert "<strong>" not in document.content
    assert "帮助中心" in document.content
    assert document.published_at is not None


def test_chunk_document_preserves_metadata() -> None:
    path = Path(__file__).parents[2] / "fixtures" / "missing.md"
    document = KnowledgeDocument(
        article_id="123",
        title="提现问题",
        category="充值与提现",
        section="问题排除",
        source_url="https://example.com/123",
        file_path=path.as_posix(),
        published_at=None,
        crawled_at=None,
        content="## 网络确认\n\n" + "需要等待网络确认。" * 80,
        content_hash="hash",
    )

    chunks = chunk_document(document, max_chars=200, overlap_chars=20)

    assert len(chunks) > 1
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(len(chunk.content) <= 200 for chunk in chunks)
    assert all("标题：提现问题" in chunk.embedding_text for chunk in chunks)
    assert all("章节：网络确认" in chunk.embedding_text for chunk in chunks)


def test_parse_document_rejects_missing_front_matter(tmp_path: Path) -> None:
    path = tmp_path / "invalid.md"
    path.write_text("# 标题\n\n正文", encoding="utf-8")

    with pytest.raises(DocumentParseError, match="front matter"):
        parse_document(path, root=tmp_path)
