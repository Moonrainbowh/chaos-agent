from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus, urljoin

import httpx


@dataclass(frozen=True)
class WebResult:
    layer: str
    url: str
    status_code: int | None
    title: str | None
    content: str
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "url": self.url,
            "status_code": self.status_code,
            "title": self.title,
            "content": self.content,
            "truncated": self.truncated,
        }


class WebAccessService:
    """Bounded HTTP/site API/browser facade; policy approval remains in Host."""

    def __init__(self, client: httpx.AsyncClient | None = None, *, max_bytes: int = 256_000) -> None:
        self.client = client
        self._owned_client = False
        self.max_bytes = max(1024, min(max_bytes, 2_000_000))

    async def search(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        """Layer 1: DuckDuckGo HTML search with bounded extraction."""
        if not query.strip():
            raise ValueError("query must be non-blank")
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        response = await self._get(url)
        text = self._decode(response)
        results: list[dict[str, Any]] = []
        for match in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.I | re.S):
            href = html.unescape(match.group(1))
            title = re.sub(r"<[^>]+>", "", html.unescape(match.group(2))).strip()
            results.append({"title": title, "url": href, "source": "duckduckgo"})
            if len(results) >= max(1, min(max_results, 20)):
                break
        return results

    async def fetch(self, url: str) -> WebResult:
        """Layer 1: fetch one public page without following redirects."""
        response = await self._get(url)
        body = response.content[: self.max_bytes]
        truncated = len(response.content) > len(body)
        text = body.decode(response.encoding or "utf-8", errors="replace")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1)))) .strip() if title_match else None
        return WebResult("web_fetch", str(response.url), response.status_code, title, text, truncated)

    async def query_site(self, site: str, identifier: str) -> WebResult:
        """Layer 2: GitHub and arXiv JSON/XML APIs, never HTML scraping."""
        if site == "github":
            url = "https://api.github.com/repos/" + identifier.strip("/")
            headers = {"Accept": "application/vnd.github+json"}
        elif site == "arxiv":
            url = "https://export.arxiv.org/api/query?search_query=id:" + quote_plus(identifier)
            headers = {"Accept": "application/atom+xml"}
        else:
            raise ValueError("site must be github or arxiv")
        response = await self._get(url, headers=headers)
        return WebResult(f"{site}_api", str(response.url), response.status_code, None, self._decode(response), len(response.content) > self.max_bytes)

    async def retrieve(
        self,
        *,
        query: str | None = None,
        url: str | None = None,
        site: str | None = None,
        identifier: str | None = None,
        verify_results: int = 1,
    ) -> dict[str, Any]:
        """Layer-aware orchestrator: site API/URL fetch/search in deterministic order."""
        supplied = sum(value is not None for value in (query, url, site))
        if supplied != 1:
            raise ValueError("provide exactly one of query, url, or site")
        if site is not None:
            if not identifier:
                raise ValueError("identifier is required for site queries")
            return {"layer": "site_api", "result": (await self.query_site(site, identifier)).as_dict()}
        if url is not None:
            return {"layer": "web_fetch", "result": (await self.fetch(url)).as_dict()}
        candidates = await self.search(query or "")
        verified: list[dict[str, Any]] = []
        for candidate in candidates[: max(0, min(verify_results, 3))]:
            try:
                verified.append((await self.fetch(candidate["url"])).as_dict())
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                candidate["fetch_error"] = str(exc)
        return {"layer": "web_search", "query": query, "candidates": candidates, "verified": verified}

    async def browser_fetch(self, url: str, *, wait_ms: int = 1000) -> WebResult:
        """Layer 4: optional Playwright browser, with no credential persistence."""
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed; use MCP or web_fetch") from exc
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                if wait_ms:
                    await page.wait_for_timeout(min(wait_ms, 10_000))
                title = await page.title()
                content = (await page.locator("body").inner_text())[: self.max_bytes]
                return WebResult("browser", page.url, 200, title, content, len(content) >= self.max_bytes)
            finally:
                await browser.close()

    async def _get(self, url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        if self.client is None:
            self.client = httpx.AsyncClient(follow_redirects=False)
            self._owned_client = True
        response = await self.client.get(url, headers=headers, timeout=httpx.Timeout(20.0, connect=10.0))
        if 300 <= response.status_code < 400:
            raise RuntimeError("redirect received; follow-up URL must be explicitly approved")
        return response

    async def aclose(self) -> None:
        if self._owned_client and self.client is not None:
            await self.client.aclose()
            self.client = None

    def _decode(self, response: httpx.Response) -> str:
        return response.content[: self.max_bytes].decode(response.encoding or "utf-8", errors="replace")
