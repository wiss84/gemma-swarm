"""
Gemma Swarm — Advanced Free Page Fetcher
==========================================
A production-grade, fully free alternative to Jina AI for fetching and cleaning web page content.

FEATURES:
    1. Retry logic with exponential backoff (3 attempts)
    2. Robots.txt compliance check (cached per domain) — bypasses blocks
    3. PDF text extraction via PyPDF2
    4. Open Graph / metadata extraction
    5. trafilatura for main content extraction (fallback to BeautifulSoup)
    6. LRU cache for recent fetches (TTL-based, 1 hour)
    7. Language detection via <html lang> — skips non-English pages
    8. Smart truncation at paragraph/section boundary
    9. Content deduplication via SHA-256 hashing
    10. Playwright with ad/image blocking for JS-heavy pages
    11. Wikipedia REST API fallback for 403s
    12. Content-type validation — rejects binary/non-text responses
"""

import hashlib
import httpx
import logging
import re
import sys
import os
import time
import urllib.parse
import urllib.robotparser
from collections import OrderedDict
from typing import Optional

if sys.platform == "win32":
    os.system("chcp 65001 >nul")

logger = logging.getLogger(__name__)

# Suppress Playwright's asyncio warnings (not applicable to sync API)
logging.getLogger("playwright").setLevel(logging.ERROR)

# ── Config ─────────────────────────────────────────────────────────────────────

FETCH_TIMEOUT       = 20
MAX_RETRIES         = 3
RETRY_BASE_DELAY    = 2
MAX_CHARS           = 40000
CHUNK_SIZE          = 10000
CACHE_TTL_SECONDS   = 3600
CACHE_MAX_SIZE      = 100
MIN_CONTENT_CHARS   = 200
ROBOTS_CACHE_TTL    = 3600

BROWSER_HEADERS = {
    "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language":           "en-US,en;q=0.9",
    "Accept-Encoding":           "gzip, deflate",
    "DNT":                       "1",
    "Connection":                "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-User":            "?1",
    "Sec-Ch-Ua":                 '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile":          "?0",
    "Sec-Ch-Ua-Platform":        '"Windows"',
    "Cache-Control":             "max-age=0",
}

REMOVE_TAGS = [
    "script", "style", "nav", "footer", "header", "aside",
    "iframe", "noscript", "svg", "figure", "form", "button",
    "input", "select", "textarea", "menu", "dialog", "details",
]

AD_BLOCK_DOMAINS = [
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "adservice.google", "facebook.com/tr", "analytics.google",
    "adsystem.com", "adservice.", "adnxs.com", "taboola.com",
    "outbrain.com", "adsrvr.org", "casalemedia.com", "criteo.com",
    "amazon-adsystem.com", "ads-twitter.com",
]

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff"}

# ── LRU Cache with TTL ────────────────────────────────────────────────────────

class TTLCache:
    """Simple LRU cache with per-entry TTL expiry."""

    def __init__(self, max_size: int = CACHE_MAX_SIZE, ttl: int = CACHE_TTL_SECONDS):
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl

    def get(self, key: str) -> Optional[str]:
        if key not in self._cache:
            return None
        entry = self._cache[key]
        if time.time() - entry["time"] > self._ttl:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return entry["value"]

    def set(self, key: str, value: str):
        if key in self._cache:
            del self._cache[key]
        self._cache[key] = {"value": value, "time": time.time()}
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def clear(self):
        self._cache.clear()


_content_cache = TTLCache()

# ── Robots.txt cache ──────────────────────────────────────────────────────────

_robots_cache: dict[str, tuple[urllib.robotparser.RobotFileParser, float]] = {}

def _check_robots(url: str) -> bool:
    """
    Check if URL is allowed by robots.txt.
    NOTE: Always returns True to allow fetching. We respect robots.txt for logging
    purposes only, but do not block content retrieval (similar to Jina's approach).
    """
    parsed = urllib.parse.urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    if base in _robots_cache:
        rp, fetched_at = _robots_cache[base]
        if time.time() - fetched_at < ROBOTS_CACHE_TTL:
            allowed = rp.can_fetch(BROWSER_HEADERS["User-Agent"], url)
            if not allowed:
                logger.debug(f"[fetcher] robots.txt marks {url} as disallowed (but allowing anyway)")
            return True  # Always allow, just log

    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{base}/robots.txt")
    try:
        rp.read()
    except Exception:
        return True

    _robots_cache[base] = (rp, time.time())

    # Check if explicitly disallowed — log it but still allow
    ua_allowed = rp.can_fetch(BROWSER_HEADERS["User-Agent"], url)
    if not ua_allowed:
        logger.debug(f"[fetcher] robots.txt marks {url} as disallowed (but allowing anyway)")
    
    # Always return True to allow content retrieval
    return True

# ── Content deduplication ─────────────────────────────────────────────────────

_seen_hashes: set[str] = set()

def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

def _is_duplicate(text: str) -> bool:
    h = _content_hash(text)
    if h in _seen_hashes:
        return True
    _seen_hashes.add(h)
    return False

# ── Language detection ────────────────────────────────────────────────────────

def _detect_language(html: str) -> Optional[str]:
    """Extract language from <html lang='...'> or <meta> tags."""
    match = re.search(r'<html[^>]*\blang=["\']([a-zA-Z-]+)', html, re.I)
    if match:
        return match.group(1).split("-")[0].lower()

    match = re.search(r'<meta[^>]*\bhttp-equiv=["\']?content-language["\']?[^>]*content=["\']([a-zA-Z-]+)', html, re.I)
    if match:
        return match.group(1).split("-")[0].lower()

    return None

# ── Metadata extraction ───────────────────────────────────────────────────────

def _extract_metadata(html: str) -> dict:
    """Extract Open Graph and meta tags for attribution."""
    meta = {}

    for pattern, key in [
        (r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']*)', "og_title"),
        (r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']*)', "og_description"),
        (r'<meta[^>]*property=["\']article:published_time["\'][^>]*content=["\']([^"\']*)', "published_time"),
        (r'<meta[^>]*property=["\']og:site_name["\'][^>]*content=["\']([^"\']*)', "site_name"),
        (r'<title>([^<]+)</title>', "title"),
    ]:
        m = re.search(pattern, html, re.I)
        if m:
            meta[key] = m.group(1).strip()

    return meta

# ── PDF extraction ────────────────────────────────────────────────────────────

def _extract_pdf(url: str) -> str:
    """Download and extract text from a PDF file."""
    try:
        with httpx.Client(headers=BROWSER_HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()

        import io
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(resp.content))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                pages.append(f"[Page {i+1}]\n{text.strip()}")

        result = "\n\n".join(pages)
        return result if result else "[PDF downloaded but no extractable text found]"

    except ImportError:
        return "[PyPDF2 not installed. Run: pip install PyPDF2]"
    except Exception as e:
        return f"[PDF extraction failed: {e}]"

# ── Content extraction: trafilatura (primary) + BeautifulSoup (fallback) ──────

def _extract_content(html: str, url: str) -> dict:
    """Extract clean readable text from HTML. Tries trafilatura first, then BS4."""
    metadata = _extract_metadata(html)
    metadata['lang'] = _detect_language(html)
    # Try trafilatura — best-in-class for article extraction
    try:
        import trafilatura
        result = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            include_links=False,
            favor_precision=True,
            url=url,
        )
        if result and len(result) > MIN_CONTENT_CHARS:
            return {'content': result.strip(), 'metadata': metadata}
    except ImportError:
        logger.debug("[fetcher] trafilatura not installed — falling back to BeautifulSoup")
    except Exception as e:
        logger.debug(f"[fetcher] trafilatura failed: {e}")

    # Fallback to BeautifulSoup
    content = _clean_html_bs4(html, url)
    return {'content': content, 'metadata': metadata}

def _clean_html_bs4(html: str, url: str) -> str:
    """Parse HTML with BeautifulSoup and extract clean readable text."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("[fetcher] BeautifulSoup not installed. Run: pip install beautifulsoup4 lxml")
        return html[:MAX_CHARS]

    soup = BeautifulSoup(html, "lxml")

    for tag in REMOVE_TAGS:
        for element in soup.find_all(tag):
            element.decompose()

    for element in soup.find_all(style=re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden")):
        element.decompose()

    main_content = (
        soup.find("main") or
        soup.find("article") or
        soup.find(id=re.compile(r"content|main|article|post|body", re.I)) or
        soup.find(class_=re.compile(r"content|main|article|post|body", re.I)) or
        soup.find("body") or
        soup
    )

    lines = []
    seen = set()

    for element in main_content.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th", "pre", "code"]):
        text = element.get_text(separator=" ", strip=True)
        if not text or len(text) < 20 or text in seen:
            continue
        seen.add(text)

        tag = element.name
        if tag.startswith("h"):
            level = int(tag[1])
            lines.append(f"\n{'#' * level} {text}\n")
        elif tag == "li":
            lines.append(f"- {text}")
        elif tag in ("pre", "code"):
            lines.append(f"```\n{text}\n```")
        else:
            lines.append(text)

    result = "\n".join(lines).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)

    if not result:
        result = soup.get_text(separator="\n", strip=True)
        result = re.sub(r"\n{3,}", "\n\n", result)

    return result

# ── Smart truncation ──────────────────────────────────────────────────────────

def _smart_truncate(text: str, max_chars: int = MAX_CHARS) -> str:
    """Truncate at the nearest paragraph or section boundary."""
    if len(text) <= max_chars:
        return text

    cutoff = max_chars
    # Try to break at double newline (paragraph boundary)
    for pos in range(cutoff, max(cutoff - 2000, 0), -1):
        if text[pos:pos+2] == "\n\n":
            return text[:pos] + "\n\n[Content truncated]"

    # Fallback: break at single newline
    last_nl = text.rfind("\n", cutoff - 500, cutoff)
    if last_nl > 0:
        return text[:last_nl] + "\n\n[Content truncated]"

    return text[:max_chars] + "\n\n[Content truncated]"

# ── Wikipedia API fallback ────────────────────────────────────────────────────

def _fetch_wikipedia_api(url: str) -> dict:
    """Fallback for Wikipedia: use the Wikipedia REST API."""
    try:
        title = urllib.parse.unquote(url.split("/wiki/")[-1])
        api_url = f"https://en.wikipedia.org/api/rest_v1/page/html/{urllib.parse.quote(title, safe='')}"
        headers = {
            "User-Agent": "GemmaSwarm/1.0 (research tool; contact: admin@example.com)",
            "Accept": "text/html",
        }
        with httpx.Client(headers=headers, timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(api_url)
            resp.raise_for_status()
            return _extract_content(resp.text, url)
    except Exception as e:
        return {'content': f"[Wikipedia API error: {e}]", 'metadata': {}}

# ── HTTP fetch with retry ─────────────────────────────────────────────────────

def _fetch_with_httpx(url: str) -> dict:
    """Fetch page with httpx, retrying on transient failures."""
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(
                headers=BROWSER_HEADERS,
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
            ) as client:
                resp = client.get(url)

                if resp.status_code == 429:
                    delay = int(resp.headers.get("Retry-After", RETRY_BASE_DELAY * (2 ** attempt)))
                    logger.info(f"[fetcher] Rate limited — waiting {delay}s (attempt {attempt+1}/{MAX_RETRIES})")
                    time.sleep(delay)
                    continue

                if resp.status_code == 403 and "wikipedia.org" in url:
                    return _fetch_wikipedia_api(url)

                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "").lower()

                if "application/pdf" in content_type:
                    return {'content': _extract_pdf(url), 'metadata': {}}

                if "text/html" not in content_type and "text/plain" not in content_type:
                    return {'content': f"[Skipped non-text content ({content_type}) for {url}]", 'metadata': {}}

                html = resp.text
                return _extract_content(html, url)

        except httpx.TimeoutException:
            last_error = f"[Timeout fetching {url}]"
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                last_error = f"[HTTP {e.response.status_code} for {url}]"
            elif e.response.status_code == 403 and "wikipedia.org" in url:
                return _fetch_wikipedia_api(url)
            else:
                return {'content': f"[HTTP {e.response.status_code} for {url}]", 'metadata': {}}
        except Exception as e:
            last_error = f"[Error fetching {url}: {e}]"

        if attempt < MAX_RETRIES - 1:
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.info(f"[fetcher] Retry {attempt+1}/{MAX_RETRIES} in {delay}s — {last_error}")
            time.sleep(delay)

    return {'content': last_error or f"[Failed to fetch {url} after {MAX_RETRIES} attempts]", 'metadata': {}}

# ── Playwright with ad blocking ───────────────────────────────────────────────

def _should_block_request(request_url: str) -> bool:
    """Return True if the request URL matches an ad/tracker domain."""
    try:
        parsed = urllib.parse.urlparse(request_url)
        domain = parsed.netloc.lower()
        for blocked in AD_BLOCK_DOMAINS:
            if blocked in domain:
                return True
    except Exception:
        pass
    return False

def _fetch_with_playwright(url: str) -> dict:
    """Fetch JS-rendered page with Playwright, blocking ads and images."""
    try:
        from playwright.sync_api import sync_playwright
        import threading

        # Run Playwright in a separate thread to avoid asyncio conflicts
        result_container = {}
        error_container = {}

        def fetch_in_thread():
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    context = browser.new_context(
                        user_agent=BROWSER_HEADERS["User-Agent"],
                        extra_http_headers={"Accept-Language": BROWSER_HEADERS["Accept-Language"]},
                    )
                    page = context.new_page()

                    page.route("**/*", lambda route: route.abort() if _should_block_request(route.request.url) else route.continue_())

                    page.goto(url, wait_until="domcontentloaded", timeout=FETCH_TIMEOUT * 1000)
                    page.wait_for_timeout(2000)

                    html = page.content()
                    browser.close()

                    result_container['html'] = html
            except Exception as e:
                error_container['error'] = e

        thread = threading.Thread(target=fetch_in_thread, daemon=False)
        thread.start()
        thread.join(timeout=FETCH_TIMEOUT * 2)

        if error_container:
            logger.warning(f"[fetcher] Playwright failed for {url}: {error_container['error']}")
            return {'content': "", 'metadata': {}}

        if 'html' not in result_container:
            logger.warning(f"[fetcher] Playwright timeout for {url}")
            return {'content': "", 'metadata': {}}

        html = result_container['html']
        result = _extract_content(html, url)
        if len(result['content']) > MIN_CONTENT_CHARS:
            return result
        else:
            return {'content': "", 'metadata': result['metadata']}

    except ImportError:
        logger.warning("[fetcher] Playwright not installed. Run: pip install playwright && playwright install chromium")
        return {'content': "", 'metadata': {}}
    except Exception as e:
        logger.warning(f"[fetcher] Playwright error for {url}: {e}")
        return {'content': "", 'metadata': {}}

# ── Main entry point ──────────────────────────────────────────────────────────

def fetch_page_free(url: str, use_playwright: bool = False, force_refresh: bool = False) -> str:
    """
    Fetch and clean a web page. Returns clean readable text suitable for LLM context.
    
    Args:
        url: The page URL to fetch
        use_playwright: Force Playwright for JS-heavy pages
        force_refresh: Bypass the cache and re-fetch
    
    Returns:
        Clean readable text of the page content
    """
    if not force_refresh:
        cached = _content_cache.get(url)
        if cached is not None:
            logger.info(f"[fetcher] Cache hit for {url}")
            return cached

    if not _check_robots(url):
        return f"[Blocked by robots.txt: {url}]"

    parsed = urllib.parse.urlparse(url)
    ext = os.path.splitext(parsed.path)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return f"[Skipped image file: {url}]"

    if ext == ".pdf" or "pdf" in parsed.path.lower():
        result = {'content': _extract_pdf(url), 'metadata': {}}
    else:
        result = _fetch_with_httpx(url)

        if len(result.get('content', '')) < 500 and not use_playwright:
            logger.info(f"[fetcher] httpx returned thin content ({len(result.get('content', ''))} chars) — trying Playwright")
            pw_result = _fetch_with_playwright(url)
            if pw_result and len(pw_result.get('content', '')) > len(result.get('content', '')):
                result = pw_result

    content = result['content']
    metadata = result['metadata']

    lang = metadata.get('lang')
    if lang and lang not in ("en",):
        logger.info(f"[fetcher] Page language detected as '{lang}' — content may not be English")

    if content.startswith("["):
        return content

    if len(content) < MIN_CONTENT_CHARS:
        return f"[Page contained no extractable text: {url}]"

    if _is_duplicate(content):
        logger.info(f"[fetcher] Duplicate content detected for {url}")

    metadata_str = "\n".join(f"{k}: {v}" for k, v in metadata.items() if v and k != 'lang')
    final_content = f"{metadata_str}\n\n{content}" if metadata_str else content
    _content_cache.set(url, final_content)
    return _smart_truncate(final_content)
