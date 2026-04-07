"""
Gemma Swarm — Autonomous Researcher
======================================
Runs on configured interval (every N days).
For each configured research topic:
  1. Search web for 20 results (no LLM)
  2. LLM picks 3 best URLs to read
  3. Fetch those pages (no LLM)
  4. LLM synthesizes content into a research summary
  5. Creates a formatted dated Google Doc with the summary
  6. Posts doc link to autonomous Slack channel
  7. Returns synthesis for linkedin_drafter to use

2 LLM calls per topic. 8 second sleep between calls.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SLEEP_BETWEEN_CALLS = 30
MAX_FETCH_URLS      = 3
FETCH_CHAR_LIMIT    = 10000


def run(slack_client, autonomous_channel_id: str) -> list[dict]:
    """
    Run research for all configured topics.
    Returns list of {topic, synthesis, doc_link} for linkedin_drafter to use.
    """
    from autonomous.settings import load_settings, save_settings
    from autonomous.jobs.activity_logger import log

    settings = load_settings()
    topics   = [t.strip() for t in settings["research"].get("topics", []) if t.strip()]

    if not topics:
        logger.info("[researcher] No topics configured — skipping.")
        return []

    results = []

    for i, topic in enumerate(topics):
        logger.info(f"[researcher] Starting research for topic: {topic}")
        result = _research_topic(topic, slack_client, autonomous_channel_id)
        if result:
            results.append(result)
            log("researcher", f"Research doc created: {topic}", "✅")
        else:
            log("researcher", f"Research failed for: {topic}", "❌")

        if i < len(topics) - 1:
            logger.info(f"[researcher] Sleeping {SLEEP_BETWEEN_CALLS}s before next topic...")
            time.sleep(SLEEP_BETWEEN_CALLS)

    settings["research"]["last_run"] = datetime.now(timezone.utc).date().isoformat()
    save_settings(settings)

    return results


def _research_topic(topic: str, slack_client, autonomous_channel_id: str) -> dict | None:
    """Research a single topic. Returns {topic, synthesis, doc_link} or None."""
    from autonomous import pipeline_agent
    from autonomous.research_history import get_excluded_urls, get_latest_summary, add_entry
    from tools.web_search_tool import search_web, fetch_page
    from tools.docs_api import docs_create_formatted

    # ── Check for previous research on this topic ──────────────────────────────
    excluded_urls = get_excluded_urls(topic)
    prev_summary  = get_latest_summary(topic)

    if excluded_urls:
        logger.info(f"[researcher] Found {len(excluded_urls)} previously used URLs for '{topic}' — will exclude.")
    if prev_summary:
        logger.info(f"[researcher] Found previous summary for '{topic}' — will build on it.")

    # ── Step 1: Search ─────────────────────────────────────────────────────────
    try:
        logger.info(f"[researcher] Searching: {topic}")
        search_results = search_web.invoke({"query": f"latest trends {topic} 2026"})
    except Exception as e:
        logger.error(f"[researcher] Search failed for '{topic}': {e}")
        return None

    # ── Step 2: LLM picks best URLs (excluding previously used ones) ────────────
    exclusion_text = ""
    if excluded_urls:
        exclusion_list = "\n".join(f"- {u}" for u in sorted(excluded_urls))
        exclusion_text = f"""

IMPORTANT — DO NOT select any of these URLs. You have already used them in recent research on this topic:
{exclusion_list}

You MUST choose different URLs to provide fresh perspectives."""

    url_prompt = f"""You are a research assistant. The user wants to learn about the latest trends on this topic: "{topic}"

Below are web search results. Each has a title, URL, and snippet.

{search_results}

Your job: Select the {MAX_FETCH_URLS} best URLs to read in full.
Choose URLs most likely to contain detailed, recent, relevant content about "{topic}".
Avoid: news aggregators, social media pages, login-required pages, sponsored content, YouTube.{exclusion_text}

Respond with ONLY a JSON array of URLs. No explanation, no other text.
Example: ["https://example.com/article1", "https://example.com/article2"]"""

    time.sleep(SLEEP_BETWEEN_CALLS)
    url_response = pipeline_agent.ask(url_prompt)

    urls = _parse_url_list(url_response)
    if not urls:
        logger.error(f"[researcher] LLM returned no valid URLs for '{topic}': {url_response[:200]}")
        return None

    logger.info(f"[researcher] Selected {len(urls)} URLs to fetch for '{topic}'")

    # ── Step 3: Fetch pages ────────────────────────────────────────────────────
    fetched_content = []
    for url in urls[:MAX_FETCH_URLS]:
        try:
            logger.info(f"[researcher] Fetching: {url}")
            content = fetch_page.invoke({"url": url})
            fetched_content.append(f"--- Source: {url} ---\n{content[:FETCH_CHAR_LIMIT]}")
            time.sleep(2)
        except Exception as e:
            logger.error(f"[researcher] Fetch failed for {url}: {e}")
            continue

    if not fetched_content:
        logger.error(f"[researcher] No pages fetched for '{topic}'")
        return None

    combined_content = "\n\n".join(fetched_content)

    # ── Step 4: LLM synthesizes (building on previous research if available) ───
    context_text = ""
    if prev_summary:
        context_text = f"""

Here is a summary from your previous research on this topic (from a recent run):
---
{prev_summary}
---

Build on this research. Focus on NEW developments, trends, or perspectives that were NOT covered in the previous summary.
If there is nothing new, acknowledge that and highlight what remains relevant."""

    synthesis_prompt = f"""You are a research analyst. The user wants a summary of the latest trends on this topic: "{topic}"

Below is content fetched from {len(fetched_content)} web pages:

{combined_content}{context_text}

Your job: Write a clear, structured research summary with these exact sections:

## Key Trends
- trend 1
- trend 2
- trend 3

## Notable Developments
Write 2-3 paragraphs here.

## Sources
- https://source1.com
- https://source2.com

Keep it factual. Do not invent information not present in the content above.
Use the ## and - markers exactly as shown above so the document can be formatted correctly."""

    time.sleep(SLEEP_BETWEEN_CALLS)
    synthesis = pipeline_agent.ask(synthesis_prompt)

    if not synthesis or synthesis.startswith("[LLM error"):
        logger.error(f"[researcher] Synthesis failed for '{topic}'")
        return None

    # ── Step 5: Create formatted Google Doc ────────────────────────────────────
    date_str  = datetime.now().strftime("%Y-%m-%d")
    doc_title = f"{topic} — Research — {date_str}"
    doc_content = (
        f"## {topic} — Research Report\n\n"
        f"Generated by Gemma Swarm Autonomous Pipeline — {date_str}\n\n"
        f"{synthesis}"
    )

    try:
        doc  = docs_create_formatted(title=doc_title, content=doc_content)
        link = doc["link"]
        logger.info(f"[researcher] Formatted doc created: {link}")
    except Exception as e:
        logger.error(f"[researcher] Doc creation failed for '{topic}': {e}")
        return None

    # ── Step 6: Post to Slack ──────────────────────────────────────────────────
    try:
        slack_client.chat_postMessage(
            channel=autonomous_channel_id,
            text=(
                f"🔍 *Research complete: {topic}*\n"
                f"📄 <{link}|Open Research Doc — {date_str}>"
            ),
            mrkdwn=True,
        )
    except Exception as e:
        logger.error(f"[researcher] Slack post failed: {e}")

    # ── Step 7: Save to research history ───────────────────────────────────────
    add_entry(
        topic=topic,
        date=date_str,
        urls=urls,
        synthesis_summary=synthesis,
    )

    return {
        "topic":     topic,
        "synthesis": synthesis,
        "doc_link":  link,
    }


def _parse_url_list(response: str) -> list[str]:
    """Extract a list of URLs from the LLM response."""
    match = re.search(r"\[.*?\]", response, re.DOTALL)
    if match:
        try:
            urls = json.loads(match.group())
            return [u for u in urls if isinstance(u, str) and u.startswith("http")]
        except json.JSONDecodeError:
            pass
    urls = re.findall(r"https?://[^\s\"\]]+", response)
    return urls[:MAX_FETCH_URLS]
