"""
Gemma Swarm — Universal Context Tracker
========================================
Estimates token usage for any agent and writes to a shared JSON file.

Token estimation:
    tokens = (system_prompt_chars + message_chars [+ tool_schema_chars]) / 3.2

Usage:
    from agents_utils.context_tracker import snapshot_context_usage

    snapshot_context_usage(
        session_id=thread_id,
        project_name="coding\\myproject",  # or "assistant\\myproject"
        messages=state["messages"],
        system_prompt=system_prompt_string,
        model="gemma-4-26b-a4b-it",
        include_tool_schemas=True,   # False for supervisor
        workspace_path=workspace,     # only needed if include_tool_schemas=True
        agent_notes_enabled=True,     # only needed if include_tool_schemas=True
        task_complete=False,          # True to reset counter after this turn
    )
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from agents_utils.config import PROJECT_ROOT, MODEL_CONTEXT_WINDOWS

logger = logging.getLogger(__name__)

CONTEXT_USAGE_FILE = PROJECT_ROOT / "agent_context_usage.json"
TOKENS_PER_CHAR    = 3.2          # conservative estimate for Gemma 4 code/English mix
DEFAULT_MAX_CTX    = 256_000


# ── Tool schema char estimator (coding agent only) ─────────────────────────────

def _estimate_tool_schema_chars(agent_notes_enabled: bool = True, workspace_path: str = "") -> int:
    """
    Instantiate a throw-away CodingAgent solely to serialise its tool schemas
    and count the characters. Only call this for agents that actually use tools.
    """
    try:
        from coding_agent.agent import CodingAgent
        agent = CodingAgent(
            workspace_path=workspace_path,
            agent_notes_enabled=agent_notes_enabled,
        )
        schemas = agent._build_tools_schema()
        return len(json.dumps(schemas))
    except Exception as e:
        logger.warning(f"[context_tracker] Could not build tool schemas for estimation: {e}")
        return 18_000  # fallback: ~30 tools × ~600 chars each


# ── Public API ──────────────────────────────────────────────────────────────────

def snapshot_context_usage(
    session_id:          str,
    project_name:        str,
    messages:            list,
    system_prompt:       str,
    model:               str  = "",
    include_tool_schemas: bool = True,
    workspace_path:      str  = "",
    agent_notes_enabled: bool = True,
    task_complete:       bool = False,
) -> dict:
    """
    Compute current context window size, update cumulative counter, persist to JSON.

    Args:
        session_id:          LangGraph thread_id / session identifier.
        project_name:        Prefixed project name (e.g. "coding\\myapp" or "assistant\\myapp").
        messages:            Full message list for this turn (LangChain BaseMessage objects).
        system_prompt:       The system prompt string used by the agent.
        model:               Model name for context window size lookup.
        include_tool_schemas: If False, skip tool schema counting (supervisor).
        workspace_path:      Passed to CodingAgent for tool schema estimation (if needed).
        agent_notes_enabled: Whether agent notes tools are included (coding only).
        task_complete:       If True, cumulative counter resets AFTER recording this turn.

    Returns:
        The usage dict that was written for this session.
    """
    if not session_id:
        return {}

    max_ctx = MODEL_CONTEXT_WINDOWS.get(model, DEFAULT_MAX_CTX)

    # ── 1. Compute current context window ───────────────────────────────────────
    system_chars = len(system_prompt)

    message_chars = sum(
        len(m.content) if isinstance(m.content, str) else len(str(m.content))
        for m in messages
    )

    schema_chars = 0
    if include_tool_schemas:
        schema_chars = _estimate_tool_schema_chars(
            agent_notes_enabled=agent_notes_enabled,
            workspace_path=workspace_path,
        )

    total_chars    = system_chars + message_chars + schema_chars
    context_tokens = max(1, int(total_chars / TOKENS_PER_CHAR))
    percent        = round((context_tokens / max_ctx) * 100, 2)

    logger.debug(
        f"[context_tracker] session={session_id[:12]}... "
        f"sys={system_chars}c  msgs={message_chars}c  schemas={schema_chars}c  "
        f"-> {context_tokens} tokens in window"
    )

    # ── 2. Build entry and persist ───────────────────────────────────────────────
    data = _load_usage_file()

    entry = {
        "project_name":    project_name or "unknown",
        "model":           model,
        "context_tokens":  context_tokens,
        "context_percent": percent,
        "max_context":     max_ctx,
        "last_updated":    datetime.now().isoformat(timespec="seconds"),
    }

    data[session_id] = entry
    _save_usage_file(data)

    logger.info(
        f"[context_tracker] {project_name} ({session_id[:12]}...): "
        f"{percent:.1f}% used  ({context_tokens:,} / {max_ctx:,} tokens)"
    )

    # ── 3. Optional reset on task completion ────────────────────────────────────
    if task_complete:
        reset_entry = dict(entry)
        reset_entry["context_tokens"]  = 0
        reset_entry["context_percent"] = 0.0
        reset_entry["last_updated"]    = datetime.now().isoformat(timespec="seconds")
        data[session_id] = reset_entry
        _save_usage_file(data)
        logger.info(
            f"[context_tracker] {project_name}: task complete — context counter reset."
        )

    return entry


# ── File I/O helpers ────────────────────────────────────────────────────────────

def _load_usage_file() -> dict:
    try:
        if CONTEXT_USAGE_FILE.exists():
            with open(CONTEXT_USAGE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"[context_tracker] Could not read {CONTEXT_USAGE_FILE}: {e}")
    return {}


def _save_usage_file(data: dict) -> None:
    try:
        with open(CONTEXT_USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"[context_tracker] Could not write {CONTEXT_USAGE_FILE}: {e}")
