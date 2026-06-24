"""一次性抓取 Bitget 中文帮助中心指定分类的文章。"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

BASE_URL: Final = "https://www.bitget.com"
DEFAULT_OUTPUT_DIR: Final = Path(__file__).resolve().parents[1] / "data" / "bitget_support"
ARTICLE_PATH_RE: Final = re.compile(r"/zh-CN/support/articles/(\d+)")
SECTION_PATH_RE: Final = re.compile(r"/zh-CN/support/sections/(\d+)")
PUBLISHED_AT_RE: Final = re.compile(r"20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}")

CATEGORIES: Final = {
    "account_security": {
        "id": "11865590960602",
        "name": "账户与安全",
    },
    "identity_verification": {
        "id": "11865590960626",
        "name": "身份认证（KYC）",
    },
    "deposit_withdrawal": {
        "id": "11865590960506",
        "name": "加密货币充值&提币",
    },
}


@dataclass(frozen=True, slots=True)
class Section:
    id: str
    name: str
    url: str
    category_key: str
    category_name: str


@dataclass(frozen=True, slots=True)
class ArticleRef:
    id: str
    title: str
    url: str
    section: Section


@dataclass(frozen=True, slots=True)
class SavedArticle:
    id: str
    title: str
    category: str
    section: str
    source_url: str
    published_at: str | None
    file: str


class FetchError(RuntimeError):
    """页面请求或解析失败。"""


class BitgetSupportCrawler:
    def __init__(
        self,
        output_dir: Path,
        concurrency: int,
        request_delay: float,
        timeout: float,
    ) -> None:
        self.output_dir = output_dir
        self.semaphore = asyncio.Semaphore(concurrency)
        self.request_delay = request_delay
        self.request_lock = asyncio.Lock()
        self.last_request_at = 0.0
        self.client = httpx.AsyncClient(
            base_url=BASE_URL,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                ),
            },
        )

    async def __aenter__(self) -> BitgetSupportCrawler:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    async def fetch_soup(self, url: str) -> BeautifulSoup:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                async with self.semaphore:
                    await self._wait_for_request_slot()
                    response = await self.client.get(url)

                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "Bitget 暂时拒绝请求",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "html.parser")
                if soup.select_one("#support-main-area") is None:
                    raise FetchError(f"页面缺少帮助中心主体，可能触发了访问限制：{url}")
                return soup
            except (httpx.HTTPError, FetchError) as exc:
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(2 ** (attempt - 1))

        raise FetchError(f"请求失败（已重试 3 次）：{url}: {last_error}")

    async def _wait_for_request_slot(self) -> None:
        async with self.request_lock:
            elapsed = time.monotonic() - self.last_request_at
            if elapsed < self.request_delay:
                await asyncio.sleep(self.request_delay - elapsed)
            self.last_request_at = time.monotonic()

    async def discover_sections(self) -> list[Section]:
        sections: dict[str, Section] = {}
        for category_key, category in CATEGORIES.items():
            url = f"/zh-CN/support/categories/{category['id']}"
            soup = await self.fetch_soup(url)
            main = soup.select_one("#support-main-area")
            if main is None:
                raise FetchError(f"无法解析分类页面：{url}")

            for link in main.select('a[href*="/zh-CN/support/sections/"]'):
                href = link.get("href")
                if not isinstance(href, str):
                    continue
                match = SECTION_PATH_RE.search(href)
                name = link.get_text(" ", strip=True)
                if match is None or not name:
                    continue
                section = Section(
                    id=match.group(1),
                    name=name,
                    url=urljoin(BASE_URL, href),
                    category_key=category_key,
                    category_name=category["name"],
                )
                sections[f"{category_key}:{section.id}"] = section

        if not sections:
            raise FetchError("未发现任何子栏目")
        return list(sections.values())

    async def discover_articles(self, section: Section) -> list[ArticleRef]:
        soup = await self.fetch_soup(section.url)
        main = soup.select_one("#support-main-area")
        if main is None:
            raise FetchError(f"无法解析子栏目页面：{section.url}")

        articles: dict[str, ArticleRef] = {}
        for link in main.select('a[href*="/zh-CN/support/articles/"]'):
            href = link.get("href")
            if not isinstance(href, str):
                continue
            match = ARTICLE_PATH_RE.search(href)
            title = link.get_text(" ", strip=True)
            if match is None or not title:
                continue
            article = ArticleRef(
                id=match.group(1),
                title=title,
                url=urljoin(BASE_URL, href),
                section=section,
            )
            articles[article.id] = article
        return list(articles.values())

    async def save_article(self, article: ArticleRef, crawled_at: str) -> SavedArticle:
        soup = await self.fetch_soup(article.url)
        main = soup.select_one("#support-main-area")
        heading = main.select_one("h1") if main else None
        if main is None or heading is None:
            raise FetchError(f"文章标题不存在：{article.url}")

        title = heading.get_text(" ", strip=True)
        content = self._find_article_content(heading)
        published_at = self._find_published_at(heading)
        self._clean_content(content)

        section_dir = f"{article.section.id}-{slugify(article.section.name, 60)}"
        filename = f"{article.id}-{slugify(title, 90)}.md"
        path = self.output_dir / article.section.category_key / section_dir / filename
        relative_path = path.relative_to(self.output_dir.parent).as_posix()

        markdown = self._build_markdown(
            article=article,
            title=title,
            published_at=published_at,
            crawled_at=crawled_at,
            content_html=content.decode_contents().strip(),
        )
        atomic_write(path, markdown)
        return SavedArticle(
            id=article.id,
            title=title,
            category=article.section.category_name,
            section=article.section.name,
            source_url=article.url,
            published_at=published_at,
            file=relative_path,
        )

    @staticmethod
    def _find_article_content(heading: Tag) -> Tag:
        parent = heading.parent
        if not isinstance(parent, Tag):
            raise FetchError("文章标题没有父节点")

        candidates = [
            child
            for child in parent.find_all("div", recursive=False)
            if len(child.get_text(" ", strip=True)) >= 20
        ]
        if not candidates:
            raise FetchError("未找到文章正文")
        return max(candidates, key=lambda node: len(node.get_text(" ", strip=True)))

    @staticmethod
    def _find_published_at(heading: Tag) -> str | None:
        parent = heading.parent
        text = parent.get_text(" ", strip=True) if isinstance(parent, Tag) else ""
        match = PUBLISHED_AT_RE.search(text)
        return match.group(0) if match else None

    @staticmethod
    def _clean_content(content: Tag) -> None:
        for unwanted in content.select("script, style, svg, button"):
            unwanted.decompose()
        for element in content.select("[class]"):
            element.attrs.pop("class", None)
        for element in content.select("[id]"):
            element.attrs.pop("id", None)
        for link in content.select("a[href]"):
            href = link.get("href")
            if isinstance(href, str):
                link["href"] = urljoin(BASE_URL, href)
        for image in content.select("img[src]"):
            src = image.get("src")
            if isinstance(src, str):
                image["src"] = urljoin(BASE_URL, src)

    @staticmethod
    def _build_markdown(
        article: ArticleRef,
        title: str,
        published_at: str | None,
        crawled_at: str,
        content_html: str,
    ) -> str:
        metadata = {
            "article_id": article.id,
            "source_url": article.url,
            "category": article.section.category_name,
            "section": article.section.name,
            "published_at": published_at,
            "crawled_at": crawled_at,
        }
        front_matter = "\n".join(
            f"{key}: {json.dumps(value, ensure_ascii=False)}"
            for key, value in metadata.items()
        )
        return f"---\n{front_matter}\n---\n\n# {title}\n\n{content_html}\n"


def slugify(value: str, max_length: int) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value, flags=re.UNICODE)
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    return (slug[:max_length].rstrip("-_") or "untitled")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


async def crawl(args: argparse.Namespace) -> int:
    crawled_at = datetime.now(UTC).isoformat()
    output_dir = args.output.resolve()
    saved_articles: list[SavedArticle] = []
    errors: list[dict[str, str]] = []

    async with BitgetSupportCrawler(
        output_dir=output_dir,
        concurrency=args.concurrency,
        request_delay=args.delay,
        timeout=args.timeout,
    ) as crawler:
        sections = await crawler.discover_sections()
        print(f"发现 {len(sections)} 个子栏目")

        article_results = await asyncio.gather(
            *(crawler.discover_articles(section) for section in sections),
            return_exceptions=True,
        )
        articles: dict[str, ArticleRef] = {}
        for section, result in zip(sections, article_results, strict=True):
            if isinstance(result, BaseException):
                errors.append({"url": section.url, "error": str(result)})
                continue
            for article in result:
                articles[article.id] = article

        print(f"发现 {len(articles)} 篇文章")
        save_results = await asyncio.gather(
            *(crawler.save_article(article, crawled_at) for article in articles.values()),
            return_exceptions=True,
        )
        for article, result in zip(articles.values(), save_results, strict=True):
            if isinstance(result, BaseException):
                errors.append({"url": article.url, "error": str(result)})
                print(f"[失败] {article.title}: {result}", file=sys.stderr)
                continue
            saved_articles.append(result)
            print(f"[保存] {result.file}")

    manifest = {
        "source": f"{BASE_URL}/zh-CN/support",
        "crawled_at": crawled_at,
        "categories": CATEGORIES,
        "article_count": len(saved_articles),
        "error_count": len(errors),
        "articles": [asdict(item) for item in sorted(saved_articles, key=lambda item: item.file)],
        "errors": errors,
    }
    atomic_write(
        output_dir / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    print(f"完成：保存 {len(saved_articles)} 篇，失败 {len(errors)} 篇")
    return 1 if errors else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.5, help="请求最小间隔（秒）")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency 必须大于 0")
    if args.delay < 0:
        parser.error("--delay 不能小于 0")
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(crawl(parse_args())))
