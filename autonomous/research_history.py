"""
Gemma Swarm — Autonomous Research History
===========================================
Persists a log of past research runs so the researcher can avoid
re-fetching the same URLs and can build on prior summaries.

Storage: research_history.json in the project root.
Structure:
{
  "entries": [
    {
      "topic": "AI agents",
      "date": "2026-04-05",
      "urls": ["https://...", "https://..."],
      "synthesis_summary": "..."
    }
  ]
}
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

HISTORY_FILE = Path(__file__).parent.parent / "research_history.json"

DEFAULT_HISTORY = {"entries": []}


def load_history() -> dict:
    """Load research_history.json. Returns default empty structure if missing."""
    if not HISTORY_FILE.exists():
        return _deep_copy(DEFAULT_HISTORY)

    try:
        saved = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return _merge(DEFAULT_HISTORY, saved)
    except Exception as e:
        logger.error(f"[research_history] Could not load history: {e}")
        return _deep_copy(DEFAULT_HISTORY)


def save_history(data: dict):
    """Write history dict to research_history.json."""
    try:
        HISTORY_FILE.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("[research_history] History saved.")
    except Exception as e:
        logger.error(f"[research_history] Could not save history: {e}")


def add_entry(topic: str, date: str, urls: list[str], synthesis_summary: str):
    """Add a new research entry and persist it."""
    history = load_history()
    history["entries"].append({
        "topic":             topic,
        "date":              date,
        "urls":              urls,
        "synthesis_summary": synthesis_summary[:500],
    })
    save_history(history)


def get_previous_research(topic: str, max_days: int = 7) -> list[dict]:
    """
    Return entries for the given topic within the last max_days.
    Ordered newest-first.
    """
    from datetime import datetime, timedelta, timezone

    history  = load_history()
    cutoff   = (datetime.now(timezone.utc) - timedelta(days=max_days)).date().isoformat()

    matches = [
        e for e in history["entries"]
        if e["topic"].lower() == topic.lower() and e["date"] >= cutoff
    ]
    matches.sort(key=lambda e: e["date"], reverse=True)
    return matches


def get_excluded_urls(topic: str, max_days: int = 7) -> set[str]:
    """Return a set of all URLs used for this topic within max_days."""
    entries = get_previous_research(topic, max_days)
    return {url for e in entries for url in e.get("urls", [])}


def get_latest_summary(topic: str, max_days: int = 7) -> str | None:
    """Return the most recent synthesis summary for this topic, or None."""
    entries = get_previous_research(topic, max_days)
    return entries[0]["synthesis_summary"] if entries else None


def _deep_copy(d: dict) -> dict:
    return json.loads(json.dumps(d))


def _merge(defaults: dict, saved: dict) -> dict:
    result = _deep_copy(defaults)
    for key, value in saved.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result
