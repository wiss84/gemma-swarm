"""
Gemma Swarm — Token Activity Tracker
======================================
Tracks and persists cumulative token activity for the line chart in the UI.

This is SEPARATE from context_tracker.py:
    - context_tracker  → how full the model's context window currently is
    - token_activity   → running total of ALL tokens consumed in this session
                         (user input, agent responses, tool inputs, tool outputs,
                          thinking blocks — everything that passes through the model)

Usage (called from base_agent.py):
    from agents_utils.token_activity_tracker import record_token_event

    record_token_event(
        session_id="1777735999.905459",
        event_type="llm_input",    # or llm_output / tool_input / tool_output / thinking
        token_count=450,
    )

The file agent_token_activity.json is always OVERWRITTEN (not appended).
Each session entry holds:
    {
        "session_id": "...",
        "project_name": "coding\\test00",
        "model": "gemma-4-31b-it",
        "cumulative_tokens": 12345,
        "datapoints": [{"t": "ISO", "cumulative": N, "event": "llm_input"}, ...],
        "last_updated": "ISO"
    }
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from agents_utils.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

TOKEN_ACTIVITY_FILE = PROJECT_ROOT / "agent_token_activity.json"

# Estimate tokens from raw text/content.
# These are rough but consistent:
#   LLM input / output:  ~4 chars per token (Gemma tokeniser, mixed English/code)
#   Tool text content:   ~4 chars per token (usually plain text or JSON)
CHARS_PER_TOKEN = 4.0

# Max datapoints kept per session to avoid unbounded growth
MAX_DATAPOINTS = 300


def estimate_tokens(text: str) -> int:
    """Rough token estimate from character count."""
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def record_token_event(
    session_id:   str,
    event_type:   str,   # "llm_input" | "llm_output" | "tool_input" | "tool_output" | "thinking"
    token_count:  int,
    project_name: str = "",
    model:        str = "",
) -> None:
    """
    Append a token event to the session's activity log, updating the
    cumulative total. Overwrites the file on every call.

    Args:
        session_id:   The LangGraph thread_id / session identifier.
        event_type:   Category of token activity.
        token_count:  Estimated tokens for this event.
        project_name: Human-readable project name (set on first call per session).
        model:        Model name (set on first call per session).
    """
    if not session_id or token_count <= 0:
        return

    data = _load_file()

    existing = data.get(session_id, {})
    cumulative  = existing.get("cumulative_tokens", 0) + token_count
    datapoints  = existing.get("datapoints", [])

    datapoints.append({
        "t":          datetime.now().isoformat(timespec="seconds"),
        "cumulative": cumulative,
        "event":      event_type,
    })

    # Cap to avoid unbounded growth
    if len(datapoints) > MAX_DATAPOINTS:
        datapoints = datapoints[-MAX_DATAPOINTS:]

    data[session_id] = {
        "session_id":        session_id,
        "project_name":      project_name or existing.get("project_name", "unknown"),
        "model":             model        or existing.get("model", ""),
        "cumulative_tokens": cumulative,
        "datapoints":        datapoints,
        "last_updated":      datetime.now().isoformat(timespec="seconds"),
    }

    _save_file(data)


def reset_session(session_id: str) -> None:
    """Clear cumulative data for a session (called on task_complete)."""
    data = _load_file()
    if session_id in data:
        entry = data[session_id]
        data[session_id] = {
            "session_id":        session_id,
            "project_name":      entry.get("project_name", "unknown"),
            "model":             entry.get("model", ""),
            "cumulative_tokens": 0,
            "datapoints":        [],
            "last_updated":      datetime.now().isoformat(timespec="seconds"),
        }
        _save_file(data)


def get_active_session() -> dict | None:
    """Return the most-recently-updated session entry, or None."""
    try:
        data = _load_file()
        if not data:
            return None
        best = max(data.keys(), key=lambda sid: data[sid].get("last_updated", ""))
        return data[best]
    except Exception:
        return None


# ── File I/O ───────────────────────────────────────────────────────────────────

def _load_file() -> dict:
    try:
        if TOKEN_ACTIVITY_FILE.exists():
            with open(TOKEN_ACTIVITY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"[token_activity] Could not read {TOKEN_ACTIVITY_FILE}: {e}")
    return {}


def _save_file(data: dict) -> None:
    try:
        with open(TOKEN_ACTIVITY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"[token_activity] Could not write {TOKEN_ACTIVITY_FILE}: {e}")
