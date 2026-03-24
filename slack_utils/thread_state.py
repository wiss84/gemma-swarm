"""
Gemma Swarm — Thread State
============================
ThreadState dataclass, registry, and status message helpers.
Includes thread registry persistence — survives bot restarts.
"""

import json
import logging
import threading
from pathlib import Path
from dataclasses import dataclass, field
from agents_utils.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

THREAD_REGISTRY_FILE = PROJECT_ROOT / "thread_registry.json"


# ── Thread State ───────────────────────────────────────────────────────────────

@dataclass
class ThreadState:
    active:           bool            = False
    cancel_event:     threading.Event = field(default_factory=threading.Event)
    active_thread_id: str             = ""
    queued_messages:  list            = field(default_factory=list)
    workspace_path:   str             = ""
    project_name:     str             = ""
    status_ts:        str             = ""
    pending_message:  str             = ""
    pending_channel:  str             = ""
    workspace_msg_ts: str             = ""
    active_channel:   str             = ""
    # Channel ID for this thread (used to match file uploads to correct thread)
    channel_id:        str             = ""
    # File attachment paths
    email_attachment_path:    str = ""
    linkedin_attachment_path: str = ""
    context_attachment_path:   str = ""
    # LangGraph thread — may differ from Slack thread_ts when resuming old project
    langgraph_thread_ts: str           = ""

    # Interrupt handling
    interrupt_pending: bool            = False  # True when waiting for interrupt button
    interrupt_message: str            = ""     # The message that triggered interrupt
    interrupt_action:  str             = ""     # "combine", "fresh", or "queue"


_threads: dict[str, ThreadState] = {}
_threads_lock = threading.Lock()

# ── Current Active Session Variables ───────────────────────────────────────────────
# Track the currently active project for file upload handling
_current_project_name: str = ""
_current_channel_id: str = ""
_current_thread_id: str = ""


def set_current_session(project_name: str, channel_id: str, thread_id: str):
    """Set the current active session variables."""
    global _current_project_name, _current_channel_id, _current_thread_id
    _current_project_name = project_name
    _current_channel_id = channel_id
    _current_thread_id = thread_id
    logger.info(f"[thread_state] Current session set: {project_name} (channel: {channel_id}, thread: {thread_id})")


def get_current_session() -> tuple[str, str, str]:
    """Get the current active session variables."""
    return _current_project_name, _current_channel_id, _current_thread_id


# ── Thread Registry Persistence ────────────────────────────────────────────────

def _load_registry() -> dict:
    """Load thread → workspace mapping from disk."""
    if not THREAD_REGISTRY_FILE.exists():
        return {}
    try:
        return json.loads(THREAD_REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[thread_state] Could not load registry: {e}")
        return {}


def _save_registry_entry(thread_ts: str, workspace_path: str, project_name: str, channel_id: str = None):
    """Save a single thread → workspace entry to disk."""
    try:
        registry = _load_registry()
        registry[thread_ts] = {
            "workspace_path": workspace_path,
            "project_name":   project_name,
            "channel_id":     channel_id or "",
        }
        THREAD_REGISTRY_FILE.write_text(
            json.dumps(registry, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"[thread_state] Could not save registry: {e}")


def load_registry_into_threads():
    """
    Called once on startup.
    Pre-populates _threads with saved workspace paths so existing
    Slack threads resume without showing workspace selection again.
    """
    registry = _load_registry()
    if not registry:
        return

    with _threads_lock:
        for thread_ts, data in registry.items():
            if thread_ts not in _threads:
                _threads[thread_ts] = ThreadState(
                    active_thread_id = thread_ts,
                    workspace_path   = data.get("workspace_path", ""),
                    project_name     = data.get("project_name", ""),
                    channel_id       = data.get("channel_id", ""),
                )
    logger.info(f"[thread_state] Loaded {len(registry)} thread(s) from registry.")


def save_thread_workspace(thread_ts: str, workspace_path: str, project_name: str, channel_id: str = None, old_thread_id: str = None):
    """Public function called by activate_workspace after workspace is selected.
    
    Args:
        thread_ts: The thread identifier (Slack thread_ts or LangGraph thread_id)
        workspace_path: Path to workspace
        project_name: Name of project
        channel_id: The Slack channel ID where this thread exists
        old_thread_id: If provided, removes only this specific old thread before saving new one
    """
    if old_thread_id:
        try:
            registry = _load_registry()
            # Only remove the SPECIFIC old thread, not all threads with same project
            if old_thread_id in registry:
                del registry[old_thread_id]
                logger.info(f"[thread_state] Removed old registry entry {old_thread_id}")
                THREAD_REGISTRY_FILE.write_text(
                    json.dumps(registry, indent=2),
                    encoding="utf-8",
                )
        except Exception as e:
            logger.warning(f"[thread_state] Could not clean registry: {e}")
    
    _save_registry_entry(thread_ts, workspace_path, project_name, channel_id)
    logger.info(f"[thread_state] Saved thread {thread_ts} → {project_name} (channel: {channel_id})")


# ── Project Thread Lookup ─────────────────────────────────────────────────────

def get_project_original_thread(project_name: str) -> str | None:
    """
    Find the original thread_ts for a project.
    Returns the first thread_ts that uses this project name (no linked history).
    Returns None if project not found in registry.
    """
    registry = _load_registry()
    for thread_ts, data in registry.items():
        if data.get("project_name") == project_name:
            return thread_ts
    return None


# ── Thread Access ───────────────────────────────────────────────────────────────

def get_thread_state(thread_ts: str) -> ThreadState:
    with _threads_lock:
        if thread_ts not in _threads:
            _threads[thread_ts] = ThreadState(active_thread_id=thread_ts)
        return _threads[thread_ts]


def get_threads_lock() -> threading.Lock:
    return _threads_lock


def get_threads_registry() -> dict:
    return _threads


# ── Status Messages ──────────────────────────────────────────────────────────────

STATUS_MESSAGES = {
    "input_router":     "📥 Reading your message...",
    "guard_rails":      "🛡️ Checking request...",
    "task_classifier":  "🔎 Classifying task...",
    "planner":          "📋 Planning subtasks...",
    "supervisor":       "🧠 Supervisor is thinking...",
    "researcher":       "🔍 Researcher is searching the web...",
    "deep_researcher":  "🔬 Deep researcher reading pages...",
    "email_composer":   "✉️ Composing email...",
    "email_send":       "📨 Sending email...",
    "linkedin_composer": "💼 Composing LinkedIn post...",
    "linkedin_send":    "📤 Publishing LinkedIn post...",
    "memory":           "🗜️ Compressing conversation memory...",
    "human_gate":       "⏳ Waiting for your confirmation...",
    "notes_writer":     "📝 Saving progress notes...",
    "validator":        "✅ Validating response...",
    "output_formatter": "📤 Formatting response...",
}


def post_status(client, channel: str, thread_ts: str, text: str) -> str | None:
    """Post a status message. Returns its ts."""
    try:
        result = client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=text,
        )
        return result["ts"]
    except Exception as e:
        logger.error(f"[slack] Could not post status: {e}")
        return None


def delete_status(client, channel: str, ts: str):
    """Delete a status message when done."""
    if not ts:
        return
    try:
        client.chat_delete(channel=channel, ts=ts)
    except Exception as e:
        logger.warning(f"[slack] Could not delete status: {e}")


def update_status(client, channel: str, ts: str, text: str):
    """Update an existing status message."""
    if not ts:
        return
    try:
        client.chat_update(channel=channel, ts=ts, text=text)
    except Exception as e:
        logger.warning(f"[slack] Could not update status: {e}")
