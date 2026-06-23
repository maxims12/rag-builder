"""Web source loader: URL / crawl / sitemap behind one interface.

This is the web-source implementation of the ``DocumentLoader`` interface from
CONTRACT.md §4. It supports three modes (selected by ``WebSourcesConfig.web_mode``),
each returning the *same* :class:`~app.rag.loader.ParsedDocument` shape the local
loader emits — so everything downstream (chunk → embed → store) is reused unchanged.

Modes
  - ``single``: fetch each URL in ``web_urls`` and extract its main content.
  - ``crawl``: start from ``web_urls`` and follow in-domain links up to
    ``crawl_depth``, respecting ``max_pages`` and ``same_domain_only``. Uses
    ``crawl4ai`` when importable (and for JS rendering); otherwise falls back to a
    dependency-light ``httpx`` + link-extraction recursive crawler.
  - ``sitemap``: parse ``sitemap_url`` (``sitemap.xml``) and enqueue every URL,
    then fetch + extract each like ``single`` mode.

Extraction
  Main-content extraction uses ``trafilatura`` (strips nav/ads/boilerplate, which
  is critical for RAG quality on doc sites). If trafilatura is unavailable we fall
  back to a BeautifulSoup text dump. ``strip_selectors`` (extra CSS selectors to
  drop, e.g. nav/footer) are applied to the raw HTML before extraction.

JS rendering
  When ``render_js=True`` the crawl4ai/Playwright path is used if available. If it
  is not installed, we degrade gracefully to the static httpx fetch and log a
  warning (the page may be missing client-rendered content).

Metadata per record (CONTRACT.md ParsedDocument web variant):
    {source_url, title, fetched_at (ISO 8601 UTC), content_hash (SHA-256 of text)}

``content_hash`` enables incremental re-crawl: a caller (the scheduler in Phase 7)
can compare hashes to skip re-embedding unchanged pages. Within a single load we
also de-duplicate by ``content_hash`` so the same page reached by two link paths is
only emitted once.

All heavy/optional imports (trafilatura, crawl4ai, playwright, bs4) are lazy so
``import app.main`` always works even when those libraries are not installed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Set, Tuple
from urllib.parse import urldefrag, urljoin, urlparse
from xml.etree import ElementTree as ET

from app.config_schemas import WebSourcesConfig
from app.rag.loader import ParsedDocument, compute_content_hash

logger = logging.getLogger("app.rag.web_loader")

# A polite default UA so doc sites don't reject us outright.
_USER_AGENT = "Mozilla/5.0 (compatible; RAGBuilderBot/0.1; +https://example.com/bot)"

_HREF_RE = re.compile(r"""href\s*=\s*["']([^"'#>]+)["']""", re.IGNORECASE)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Optional dependency probes ────────────────────────────────────────


def _has_crawl4ai() -> bool:
    try:  # pragma: no cover - depends on optional install
        import crawl4ai  # noqa: F401

        return True
    except Exception:
        return False


# ── Extraction helpers ────────────────────────────────────────────────


def _apply_strip_selectors(html: str, selectors: List[str]) -> str:
    """Remove any DOM nodes matching the given CSS selectors from the HTML.

    Best-effort: requires BeautifulSoup. If bs4 is unavailable or no selectors
    are configured the HTML is returned unchanged.
    """
    if not selectors or not html:
        return html
    try:
        from bs4 import BeautifulSoup
    except Exception:  # pragma: no cover - bs4 always installed in this project
        return html
    soup = BeautifulSoup(html, "html.parser")
    for selector in selectors:
        try:
            for node in soup.select(selector):
                node.decompose()
        except Exception:
            # Invalid selector: skip it rather than failing the whole fetch.
            logger.debug("Invalid strip selector ignored: %s", selector)
    return str(soup)


def _extract_title(html: str) -> Optional[str]:
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            return h1.get_text(strip=True)
    except Exception:  # pragma: no cover
        pass
    return None


def _extract_main_text(html: str) -> str:
    """Extract clean main content from raw HTML.

    Prefers trafilatura (boilerplate removal); falls back to a BeautifulSoup text
    dump that drops obvious chrome (script/style/nav/header/footer).
    """
    if not html:
        return ""
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        if extracted and extracted.strip():
            return extracted.strip()
    except Exception as exc:  # pragma: no cover - optional/parse failures
        logger.debug("trafilatura extraction failed, falling back: %s", exc)

    # Fallback: BeautifulSoup text dump.
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception:  # pragma: no cover
        return ""


def _same_domain(a: str, b: str) -> bool:
    try:
        return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()
    except Exception:
        return False


def _normalize_url(url: str) -> str:
    """Drop the fragment and trailing slash noise for de-dup of crawl frontiers."""
    url, _frag = urldefrag(url)
    return url


def _extract_links(html: str, base_url: str) -> List[str]:
    """Return absolute, http(s) links found in the HTML, fragments stripped."""
    if not html:
        return []
    links: List[str] = []
    for match in _HREF_RE.finditer(html):
        href = match.group(1).strip()
        if not href or href.lower().startswith(("mailto:", "javascript:", "tel:")):
            continue
        absolute = _normalize_url(urljoin(base_url, href))
        scheme = urlparse(absolute).scheme.lower()
        if scheme in ("http", "https"):
            links.append(absolute)
    return links


def parse_sitemap(xml_text: str) -> List[str]:
    """Parse a sitemap.xml (or sitemap index) and return all ``<loc>`` URLs.

    Handles both ``<urlset>`` (page URLs) and ``<sitemapindex>`` (nested sitemap
    URLs); the caller may recurse on nested sitemaps. Namespace-agnostic so it
    works regardless of the sitemap's declared xmlns.
    """
    urls: List[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("Failed to parse sitemap XML: %s", exc)
        return urls
    for loc in root.iter():
        tag = loc.tag.rsplit("}", 1)[-1]  # strip namespace
        if tag == "loc" and loc.text and loc.text.strip():
            urls.append(loc.text.strip())
    return urls


# ── Robots.txt (best-effort) ──────────────────────────────────────────


class _RobotsChecker:
    """Tiny per-domain robots.txt allow/deny cache.

    Uses the stdlib ``urllib.robotparser``. Fetching robots is best-effort: a
    network/parse failure defaults to *allow* (so a missing robots.txt never
    blocks ingestion of a site the user explicitly asked for).
    """

    def __init__(self, enabled: bool, timeout: float) -> None:
        self.enabled = enabled
        self.timeout = timeout
        self._cache: dict[str, object] = {}

    async def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._cache.get(origin)
        if rp is None:
            rp = await asyncio.to_thread(self._load, origin)
            self._cache[origin] = rp
        if rp is False:  # failed to load -> allow
            return True
        try:
            return rp.can_fetch(_USER_AGENT, url)  # type: ignore[union-attr]
        except Exception:  # pragma: no cover
            return True

    def _load(self, origin: str):
        from urllib.robotparser import RobotFileParser

        try:
            import httpx

            resp = httpx.get(
                f"{origin}/robots.txt",
                timeout=self.timeout,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            )
            if resp.status_code >= 400:
                return False
            rp = RobotFileParser()
            rp.parse(resp.text.splitlines())
            return rp
        except Exception:
            return False


# ── Fetching ──────────────────────────────────────────────────────────


async def _fetch_html_static(url: str, timeout: float) -> Tuple[str, Optional[str]]:
    """Fetch a URL with httpx and return ``(html, error)``."""
    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if content_type and "html" not in content_type and "xml" not in content_type:
                # Non-HTML (pdf/binary) — skip; local loader handles files.
                return "", f"unsupported content-type: {content_type}"
            return resp.text, None
    except Exception as exc:
        return "", str(exc)


async def _fetch_html_rendered(url: str, timeout: float) -> Tuple[str, Optional[str]]:
    """Fetch a JS-rendered page via crawl4ai/Playwright; degrade to static.

    Returns ``(html, error)``. If the rendered path is unavailable we fall back to
    the static fetch so the caller still gets *something*.
    """
    if not _has_crawl4ai():
        logger.warning(
            "render_js requested but crawl4ai/Playwright is not installed; "
            "falling back to static fetch for %s",
            url,
        )
        return await _fetch_html_static(url, timeout)
    try:  # pragma: no cover - requires optional crawl4ai + browser
        from crawl4ai import AsyncWebCrawler

        async with AsyncWebCrawler(verbose=False) as crawler:
            result = await crawler.arun(url=url, page_timeout=int(timeout * 1000))
            html = getattr(result, "html", None) or getattr(result, "cleaned_html", "")
            if html:
                return html, None
            return "", "crawl4ai returned empty html"
    except Exception as exc:  # pragma: no cover
        logger.warning("crawl4ai render failed for %s (%s); using static fetch", url, exc)
        return await _fetch_html_static(url, timeout)


class WebExtractionResult:
    """In-between value carrying everything ``/web-sources/test`` needs."""

    def __init__(
        self,
        url: str,
        title: Optional[str],
        clean_text: str,
        raw_html_length: int,
        content_hash: str,
        fetched_at: str,
        error: Optional[str] = None,
    ) -> None:
        self.url = url
        self.title = title
        self.clean_text = clean_text
        self.raw_html_length = raw_html_length
        self.extracted_text_length = len(clean_text)
        self.content_hash = content_hash
        self.fetched_at = fetched_at
        self.error = error


async def extract_url(
    url: str,
    *,
    render_js: bool = False,
    strip_selectors: Optional[List[str]] = None,
    timeout: float = 30.0,
) -> WebExtractionResult:
    """Fetch + clean a single URL (the unit of work shared by all modes).

    This is what ``POST /web-sources/test`` previews and what every mode uses to
    turn a URL into clean text + contract metadata.
    """
    fetched_at = _iso_now()
    if render_js:
        html, error = await _fetch_html_rendered(url, timeout)
    else:
        html, error = await _fetch_html_static(url, timeout)

    raw_len = len(html or "")
    if error and not html:
        return WebExtractionResult(
            url=url,
            title=None,
            clean_text="",
            raw_html_length=raw_len,
            content_hash=compute_content_hash(""),
            fetched_at=fetched_at,
            error=error,
        )

    stripped = _apply_strip_selectors(html, strip_selectors or [])
    title = _extract_title(stripped)
    clean_text = _extract_main_text(stripped)
    return WebExtractionResult(
        url=url,
        title=title,
        clean_text=clean_text,
        raw_html_length=raw_len,
        content_hash=compute_content_hash(clean_text),
        fetched_at=fetched_at,
        error=None,
    )


def _to_parsed_document(result: WebExtractionResult) -> ParsedDocument:
    """Map an extraction result to the contract's web ParsedDocument shape."""
    return ParsedDocument(
        text=result.clean_text,
        metadata={
            "source_url": result.url,
            "title": result.title,
            "fetched_at": result.fetched_at,
            "content_hash": result.content_hash,
        },
    )


class WebSourceLoader:
    """``DocumentLoader`` for web sources (single / crawl / sitemap).

    Honours ``WebSourcesConfig``: ``web_mode``, ``web_urls``, ``sitemap_url``,
    ``crawl_depth``, ``max_pages``, ``same_domain_only``, ``render_js``,
    ``strip_selectors``, ``respect_robots_txt``, ``request_timeout_s`` and
    ``crawl_concurrency``.

    ``known_hashes`` lets an incremental re-crawl skip pages whose extracted text
    is unchanged: any page whose ``content_hash`` is in this set is dropped from
    the returned documents (Phase 7 scheduler passes the previously-seen hashes).
    """

    def __init__(
        self,
        config: WebSourcesConfig,
        known_hashes: Optional[Set[str]] = None,
    ) -> None:
        self.config = config
        self.known_hashes = known_hashes or set()
        self._timeout = float(config.request_timeout_s)
        self._concurrency = max(1, int(config.crawl_concurrency))
        self._robots = _RobotsChecker(config.respect_robots_txt, self._timeout)
        # Hashes seen during *this* load — used to drop intra-run duplicates.
        self._seen_hashes: Set[str] = set()

    # ── public interface ──────────────────────────────────────────────

    async def load(self) -> List[ParsedDocument]:
        mode = self.config.web_mode
        if mode == "single":
            urls = list(self.config.web_urls)
            results = await self._extract_many(urls)
        elif mode == "sitemap":
            urls = await self._sitemap_urls()
            results = await self._extract_many(urls[: self.config.max_pages])
        elif mode == "crawl":
            results = await self._crawl()
        else:  # pragma: no cover - schema constrains this
            logger.warning("Unknown web_mode '%s'; treating as single", mode)
            results = await self._extract_many(list(self.config.web_urls))

        docs = self._results_to_docs(results)
        logger.info(
            "WebSourceLoader (%s) produced %d document(s)", mode, len(docs)
        )
        return docs

    # ── extraction orchestration ──────────────────────────────────────

    async def _extract_one(self, url: str) -> Optional[WebExtractionResult]:
        if not await self._robots.allowed(url):
            logger.info("robots.txt disallows %s; skipping", url)
            return None
        result = await extract_url(
            url,
            render_js=self.config.render_js,
            strip_selectors=self.config.strip_selectors,
            timeout=self._timeout,
        )
        if result.error:
            logger.warning("Failed to fetch %s: %s", url, result.error)
            return None
        if not result.clean_text.strip():
            logger.info("No extractable content at %s; skipping", url)
            return None
        return result

    async def _extract_many(self, urls: Iterable[str]) -> List[WebExtractionResult]:
        """Fetch+extract a flat list of URLs with bounded concurrency."""
        unique: List[str] = []
        seen: Set[str] = set()
        for u in urls:
            nu = _normalize_url(u)
            if nu and nu not in seen:
                seen.add(nu)
                unique.append(nu)

        semaphore = asyncio.Semaphore(self._concurrency)

        async def _guarded(u: str) -> Optional[WebExtractionResult]:
            async with semaphore:
                return await self._extract_one(u)

        results = await asyncio.gather(*[_guarded(u) for u in unique])
        return [r for r in results if r is not None]

    async def _crawl(self) -> List[WebExtractionResult]:
        """Breadth-first in-domain crawl honouring depth / max_pages / domain."""
        seeds = [_normalize_url(u) for u in self.config.web_urls if u]
        if not seeds:
            return []

        max_pages = self.config.max_pages
        max_depth = self.config.crawl_depth
        same_domain = self.config.same_domain_only
        seed_domain = seeds[0]

        visited: Set[str] = set()
        collected: List[WebExtractionResult] = []
        # frontier holds (url, depth)
        frontier: List[Tuple[str, int]] = [(u, 0) for u in seeds]
        semaphore = asyncio.Semaphore(self._concurrency)

        async def _fetch_with_links(
            url: str,
        ) -> Tuple[Optional[WebExtractionResult], List[str]]:
            """Fetch a page and also return its in-page links for the frontier."""
            async with semaphore:
                if not await self._robots.allowed(url):
                    logger.info("robots.txt disallows %s; skipping", url)
                    return None, []
                if self.config.render_js:
                    html, error = await _fetch_html_rendered(url, self._timeout)
                else:
                    html, error = await _fetch_html_static(url, self._timeout)
                if error and not html:
                    logger.warning("Failed to fetch %s: %s", url, error)
                    return None, []
                links = _extract_links(html, url)
                stripped = _apply_strip_selectors(html, self.config.strip_selectors)
                clean = _extract_main_text(stripped)
                if not clean.strip():
                    return None, links
                result = WebExtractionResult(
                    url=url,
                    title=_extract_title(stripped),
                    clean_text=clean,
                    raw_html_length=len(html),
                    content_hash=compute_content_hash(clean),
                    fetched_at=_iso_now(),
                )
                return result, links

        depth = 0
        while frontier and len(collected) < max_pages and depth <= max_depth:
            # Process the current depth level as one concurrent batch.
            level = frontier
            frontier = []
            batch: List[str] = []
            for url, d in level:
                if url in visited:
                    continue
                if same_domain and not _same_domain(url, seed_domain):
                    continue
                visited.add(url)
                batch.append(url)
                if len(visited) >= max_pages and d >= 0:
                    # Cap how many we even fetch.
                    pass

            if not batch:
                depth += 1
                continue

            batch = batch[: max(0, max_pages - len(collected))] or batch[:1]
            outcomes = await asyncio.gather(*[_fetch_with_links(u) for u in batch])

            for result, links in outcomes:
                if result is not None and len(collected) < max_pages:
                    collected.append(result)
                if depth < max_depth:
                    for link in links:
                        if link in visited:
                            continue
                        if same_domain and not _same_domain(link, seed_domain):
                            continue
                        frontier.append((link, depth + 1))

            depth += 1

        return collected[:max_pages]

    async def _sitemap_urls(self) -> List[str]:
        """Fetch + parse the configured sitemap (recursing into sitemap indexes)."""
        sitemap_url = self.config.sitemap_url
        if not sitemap_url:
            logger.warning("sitemap mode selected but sitemap_url is empty")
            return []
        return await self._collect_sitemap(sitemap_url, depth=0)

    async def _collect_sitemap(self, sitemap_url: str, depth: int) -> List[str]:
        if depth > 3:  # guard against pathological sitemap-index loops
            return []
        html, error = await _fetch_html_static(sitemap_url, self._timeout)
        if error and not html:
            logger.warning("Failed to fetch sitemap %s: %s", sitemap_url, error)
            return []
        entries = parse_sitemap(html)
        page_urls: List[str] = []
        for entry in entries:
            if entry.lower().endswith(".xml"):
                # Nested sitemap (sitemap index) — recurse.
                page_urls.extend(await self._collect_sitemap(entry, depth + 1))
            else:
                page_urls.append(entry)
            if len(page_urls) >= self.config.max_pages:
                break
        return page_urls

    # ── de-dup + mapping ──────────────────────────────────────────────

    def _results_to_docs(
        self, results: List[WebExtractionResult]
    ) -> List[ParsedDocument]:
        docs: List[ParsedDocument] = []
        for result in results:
            h = result.content_hash
            if h in self.known_hashes:
                logger.info("Skipping unchanged page (known hash): %s", result.url)
                continue
            if h in self._seen_hashes:
                logger.debug("Skipping intra-run duplicate page: %s", result.url)
                continue
            self._seen_hashes.add(h)
            docs.append(_to_parsed_document(result))
        return docs
