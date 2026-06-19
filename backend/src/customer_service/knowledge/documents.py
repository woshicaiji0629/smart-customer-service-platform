"""Parse and chunk crawled Markdown knowledge documents."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from bs4 import BeautifulSoup


DEFAULT_MAX_CHARS: Final = 1_000
DEFAULT_OVERLAP_CHARS: Final = 150
FRONT_MATTER_RE: Final = re.compile(r"\A---\s*\n(?P<meta>.*?)\n---\s*\n", re.DOTALL)
HEADING_RE: Final = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$")
IMAGE_RE: Final = re.compile(r"!\[[^]]*]\([^)]*\)")
LINK_RE: Final = re.compile(r"\[([^]]+)]\([^)]*\)")


class DocumentParseError(ValueError):
    """The knowledge document does not match the expected crawler format."""


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    article_id: str
    title: str
    category: str
    section: str
    source_url: str
    file_path: str
    published_at: datetime | None
    crawled_at: datetime | None
    content: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    article_id: str
    chunk_index: int
    heading: str | None
    content: str
    embedding_text: str
    content_hash: str


def parse_document(path: Path, *, root: Path) -> KnowledgeDocument:
    raw = path.read_text(encoding="utf-8")
    match = FRONT_MATTER_RE.match(raw)
    if match is None:
        raise DocumentParseError(f"缺少 front matter: {path}")

    metadata = _parse_metadata(match.group("meta"), path)
    body = _clean_markdown(raw[match.end() :])
    lines = body.splitlines()
    title = _extract_title(lines, path)
    content = "\n".join(lines).strip()
    required = ("article_id", "category", "section", "source_url")
    missing = [key for key in required if not metadata.get(key)]
    if missing:
        raise DocumentParseError(f"缺少元数据 {', '.join(missing)}: {path}")

    return KnowledgeDocument(
        article_id=str(metadata["article_id"]),
        title=title,
        category=str(metadata["category"]),
        section=str(metadata["section"]),
        source_url=str(metadata["source_url"]),
        file_path=path.relative_to(root).as_posix(),
        published_at=_parse_datetime(metadata.get("published_at"), path),
        crawled_at=_parse_datetime(metadata.get("crawled_at"), path),
        content=content,
        content_hash=_sha256(
            "\n".join(
                [
                    title,
                    str(metadata["category"]),
                    str(metadata["section"]),
                    content,
                ]
            )
        ),
    )


def chunk_document(
    document: KnowledgeDocument,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[KnowledgeChunk]:
    if max_chars <= 0:
        raise ValueError("max_chars 必须大于 0")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars 必须大于等于 0 且小于 max_chars")

    sections = _split_sections(document.content, document.title)
    chunks: list[KnowledgeChunk] = []
    for heading, section_text in sections:
        for content in _split_section(section_text, max_chars, overlap_chars):
            embedding_text = _build_embedding_text(document, heading, content)
            chunks.append(
                KnowledgeChunk(
                    article_id=document.article_id,
                    chunk_index=len(chunks),
                    heading=heading,
                    content=content,
                    embedding_text=embedding_text,
                    content_hash=_sha256(embedding_text),
                )
            )
    if not chunks:
        raise DocumentParseError(f"文章没有可索引正文: {document.file_path}")
    return chunks


def _parse_metadata(raw: str, path: Path) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for line_number, line in enumerate(raw.splitlines(), start=2):
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            raise DocumentParseError(f"front matter 第 {line_number} 行格式错误: {path}")
        try:
            metadata[key.strip()] = json.loads(value.strip())
        except json.JSONDecodeError as exc:
            raise DocumentParseError(
                f"front matter 第 {line_number} 行不是有效 JSON 值: {path}"
            ) from exc
    return metadata


def _clean_markdown(raw: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for element in soup.select("script, style, svg, img"):
        element.decompose()
    text = soup.get_text(" ")
    text = IMAGE_RE.sub("", text)
    text = LINK_RE.sub(r"\1", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    compact: list[str] = []
    for line in lines:
        if line or (compact and compact[-1]):
            compact.append(line)
    return "\n".join(compact).strip()


def _extract_title(lines: list[str], path: Path) -> str:
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match and len(match.group("level")) == 1:
            lines.pop(index)
            return match.group("title").strip()
    raise DocumentParseError(f"缺少一级标题: {path}")


def _parse_datetime(value: object, path: Path) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DocumentParseError(f"日期字段不是字符串: {path}")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise DocumentParseError(f"日期字段格式错误 {value!r}: {path}") from exc


def _split_sections(content: str, default_heading: str) -> list[tuple[str | None, str]]:
    sections: list[tuple[str | None, str]] = []
    heading: str | None = default_heading
    body: list[str] = []
    for line in content.splitlines():
        match = HEADING_RE.match(line)
        if match:
            if body and "\n".join(body).strip():
                sections.append((heading, "\n".join(body).strip()))
            heading = match.group("title").strip()
            body = []
        else:
            body.append(line)
    if body and "\n".join(body).strip():
        sections.append((heading, "\n".join(body).strip()))
    return sections


def _split_section(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        units.extend(_split_oversized(paragraph, max_chars, overlap_chars))

    chunks: list[str] = []
    current: list[str] = []
    for unit in units:
        candidate = "\n\n".join([*current, unit])
        if current and len(candidate) > max_chars:
            chunks.append("\n\n".join(current))
            available_overlap = max(0, max_chars - len(unit) - 2)
            current = _overlap_units(
                current,
                min(overlap_chars, available_overlap),
            )
        current.append(unit)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _split_oversized(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    step = max_chars - overlap_chars
    return [text[start : start + max_chars] for start in range(0, len(text), step)]


def _overlap_units(units: list[str], overlap_chars: int) -> list[str]:
    if overlap_chars == 0:
        return []
    selected: list[str] = []
    size = 0
    for unit in reversed(units):
        if size + len(unit) > overlap_chars:
            break
        selected.append(unit)
        size += len(unit)
    if selected:
        return list(reversed(selected))
    return [units[-1][-overlap_chars:]]


def _build_embedding_text(
    document: KnowledgeDocument,
    heading: str | None,
    content: str,
) -> str:
    fields = [
        f"标题：{document.title}",
        f"分类：{document.category}",
        f"栏目：{document.section}",
    ]
    if heading and heading != document.title:
        fields.append(f"章节：{heading}")
    fields.append(f"内容：{content}")
    return "\n".join(fields)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
