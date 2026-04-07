"""
Gemma Swarm — Web Search Tools
================================
Three tools for agentic search:

1. search_web(query)        — Returns titles, URLs, snippets. No LLM.
2. fetch_page(url)          — Fetches full page via FREE fetcher (primary) or Jina (fallback), returns chunk 1. 
3. fetch_next_chunk(url)    — Returns next chunk for a previously fetched URL.

Fetching Strategy:
- PRIMARY: Advanced free fetcher (trafilatura + BeautifulSoup, caching, retry logic)
- FALLBACK: Jina Reader API (when free fetcher fails)
- BENEFIT: Highly sustainable, no token limits on primary fetcher

Large pages are split into 10,000 char chunks. The researcher reads them
sequentially by calling fetch_next_chunk until all chunks are read.
No LLM is involved in chunking — content is never altered or summarized.
"""

import os
import httpx
import logging
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from dotenv import load_dotenv
from agents_utils.web_fetcher import fetch_page_free

load_dotenv()

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

SEARCH_MAX_RESULTS = 20
FETCH_TIMEOUT      = 20
JINA_READER_URL    = "https://r.jina.ai/"
MAX_PAGE_CHARS     = 40000  # Cap raw page content before chunking
CHUNK_SIZE         = 10000  # ~2,500 tokens per chunk, safe for 15k TPM limit

# ── In-memory chunk store ──────────────────────────────────────────────────────
# Keyed by URL. Stores remaining chunks after chunk 1 is returned.
# { url: [chunk2, chunk3, ...] }
_page_chunks: dict[str, list[str]] = {}


# ── Internal Helpers ───────────────────────────────────────────────────────────

def _ddg_search(query: str, max_results: int = SEARCH_MAX_RESULTS) -> list[dict]:
    """Search using ddgs. Returns raw result dicts."""
    try:
        from ddgs import DDGS
        results = DDGS(timeout=15).text(
            query,
            region="us-en",
            safesearch="moderate",
            max_results=max_results,
            backend="auto",
        )
        return results or []
    except Exception as e:
        logger.error(f"[search_web] DuckDuckGo error: {e}")
        try:
            from ddgs import DDGS
            results = DDGS(timeout=15).text(
                query,
                region="us-en",
                safesearch="moderate",
                max_results=max_results,
                backend="html",
            )
            return results or []
        except Exception as e2:
            logger.error(f"[search_web] DuckDuckGo retry failed: {e2}")
            return []


def _fetch_raw_page(url: str) -> str:
    """
    Fetch page content. Uses free fetcher as primary, falls back to Jina.
    Returns clean markdown text.
    """
    # PRIMARY: Try free web fetcher first
    logger.info(f"[fetch_page] Trying free fetcher for: {url}")
    try:
        content = fetch_page_free(url, force_refresh=False)
        # Check if it's an error message
        if not content.startswith("["):
            logger.info(f"[fetch_page] Free fetcher succeeded ({len(content)} chars)")
            return content
        else:
            logger.warning(f"[fetch_page] Free fetcher returned error: {content}")
    except Exception as e:
        logger.warning(f"[fetch_page] Free fetcher failed: {e}")

    # FALLBACK: Use Jina if free fetcher failed
    logger.info(f"[fetch_page] Falling back to Jina for: {url}")
    jina_url = f"{JINA_READER_URL}{url}"
    headers  = {"Accept": "text/plain"}

    api_key = os.getenv("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = httpx.get(
            jina_url,
            headers=headers,
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
        content = response.text.strip()
        logger.info(f"[fetch_page] Jina fallback succeeded ({len(content)} chars)")
        return content if content else "[Jina: No content returned]"

    except httpx.TimeoutException:
        logger.error(f"[fetch_page] Jina timeout for {url}")
        return f"[Jina: Timeout fetching {url}]"
    except httpx.HTTPStatusError as e:
        logger.error(f"[fetch_page] Jina HTTP {e.response.status_code} for {url}")
        return f"[Jina: HTTP {e.response.status_code} for {url}]"
    except httpx.RequestError as e:
        logger.error(f"[fetch_page] Jina request error: {e}")
        return f"[Jina: Request error: {e}]"


def _split_into_chunks(content: str) -> list[str]:
    """Split content into CHUNK_SIZE chunks, respecting line boundaries."""
    if len(content) <= CHUNK_SIZE:
        return [content]

    chunks = []
    start  = 0

    while start < len(content):
        end = start + CHUNK_SIZE

        if end >= len(content):
            chunks.append(content[start:])
            break

        # Try to break at a newline boundary to avoid cutting mid-line
        newline_pos = content.rfind("\n", start, end)
        if newline_pos > start:
            end = newline_pos + 1

        chunks.append(content[start:end])
        start = end

    return chunks


def _format_chunk(chunk: str, current: int, total: int, url: str) -> str:
    """Format a chunk with position info and next-step instructions."""
    if total == 1:
        return f"Content from {url}:\n\n{chunk}"

    header = f"[Chunk {current}/{total} from {url}]"

    if current < total:
        footer = (
            f"\n\n[{total - current} chunk(s) remaining. "
            f"Call fetch_next_chunk with url=\"{url}\" to continue reading.]"
        )
    else:
        footer = "\n\n[You have now read the full page.]"

    return f"{header}\n\n{chunk}{footer}"


# ── Tool 1: search_web ─────────────────────────────────────────────────────────

class SearchWebInput(BaseModel):
    query: str = Field(
        description="Search query. Be specific. Example: 'langchain deep agent python example code'"
    )


@tool(args_schema=SearchWebInput)
def search_web(query: str) -> str:
    """
    Search the web and return a list of results with titles, URLs, and snippets.
    Returns up to 20 results. No page content is fetched — use fetch_page to read specific pages.
    Use this first to find relevant URLs, then decide which ones to fetch.
    If the user provides a URL directly, skip this tool and use fetch_page instead.
    """
    logger.info(f"[search_web] Query: {query}")

    results = _ddg_search(query, max_results=SEARCH_MAX_RESULTS)

    if not results:
        return "No search results found. Try rephrasing your query."

    lines = [f"Found {len(results)} results for: {query}\n"]

    for i, r in enumerate(results, 1):
        title   = r.get("title", "No title")
        url     = r.get("href", "")
        snippet = r.get("body", "")[:200]

        if not url:
            continue

        lines.append(
            f"Result {i}:\n"
            f"  Title:   {title}\n"
            f"  URL:     {url}\n"
            f"  Snippet: {snippet}\n"
        )

    return "\n".join(lines)


# ── Tool 2: fetch_page ─────────────────────────────────────────────────────────

class FetchPageInput(BaseModel):
    url: str = Field(
        description="Full URL of the page to fetch. Must start with http:// or https://"
    )


@tool(args_schema=FetchPageInput)
def fetch_page(url: str) -> str:
    """
    Fetch and read a web page. Returns the full clean content as markdown.
    For large pages, returns the first chunk and instructs you to call
    fetch_next_chunk to read the remaining chunks sequentially.
    Always use this tool directly when the user provides a URL.
    """
    logger.info(f"[fetch_page] Fetching: {url}")

    raw = _fetch_raw_page(url)

    # Return error messages as-is
    if raw.startswith("["):
        return raw

    # Cap page size before chunking
    if len(raw) > MAX_PAGE_CHARS:
        raw = raw[:MAX_PAGE_CHARS]
        logger.info(f"[fetch_page] Page capped at {MAX_PAGE_CHARS} chars for {url}")

    chunks = _split_into_chunks(raw)
    total  = len(chunks)

    logger.info(f"[fetch_page] {len(raw)} chars → {total} chunk(s) for {url}")

    if total == 1:
        # Small page — return everything at once
        _page_chunks.pop(url, None)
        return _format_chunk(chunks[0], 1, 1, url)

    # Large page — store remaining chunks and total, return first
    _page_chunks[url]                  = chunks[1:]  # Store chunks 2..N
    _page_chunks[f"{url}__total"]      = total        # Store total for progress display
    return _format_chunk(chunks[0], 1, total, url)


# ── Tool 3: fetch_next_chunk ───────────────────────────────────────────────────

class FetchNextChunkInput(BaseModel):
    url: str = Field(
        description="The same URL you passed to fetch_page. Returns the next unread chunk."
    )


@tool(args_schema=FetchNextChunkInput)
def fetch_next_chunk(url: str) -> str:
    """
    Returns the next chunk of a page previously fetched with fetch_page.
    Call this repeatedly until you receive 'You have now read the full page.'
    Only valid for URLs that were fetched in the current session.
    """
    remaining = _page_chunks.get(url)

    if not remaining:
        return (
            f"[No more chunks for {url}. "
            f"Either the page was fully read or fetch_page was not called first.]"
        )

    chunk = remaining.pop(0)

    # Calculate position: we need total to display progress
    # We don't store total explicitly, so we infer from what's left
    # chunks_left = len(remaining) after pop, so current = total - chunks_left
    # We store total in a separate key to track progress
    total_key = f"{url}__total"
    if total_key not in _page_chunks:
        # Shouldn't happen but handle gracefully
        current = "?"
        total   = "?"
    else:
        total   = _page_chunks[total_key]
        current = total - len(remaining)

    if not remaining:
        _page_chunks.pop(url, None)
        _page_chunks.pop(total_key, None)

    logger.info(f"[fetch_next_chunk] Chunk {current}/{total} for {url}")
    return _format_chunk(chunk, current, total, url)