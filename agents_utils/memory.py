"""
Gemma Swarm — Memory & Checkpointing
=======================================
Handles:
1. SQLite persistence — conversation history survives restarts
2. Token estimation — rough token count for messages
3. Workspace management — creates project folders
"""

import logging
from pathlib import Path
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver

from agents_utils.config import DB_PATH

logger = logging.getLogger(__name__)


# ── SQLite Checkpointer ────────────────────────────────────────────────────────

def get_checkpointer() -> SqliteSaver:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    return SqliteSaver(conn)



# ── Token Estimation ───────────────────────────────────────────────────────────

def estimate_messages_tokens(messages: list) -> int:
    """
    Rough token estimate for a list of messages.
    Uses same 1 token ≈ 4 chars heuristic as RateLimitHandler.
    """
    total_chars = sum(
        len(m.content) if isinstance(m.content, str) else len(str(m.content))
        for m in messages
    )
    return total_chars // 4



# ── Workspace Management ───────────────────────────────────────────────────────

def list_workspaces(workspace_root: str) -> list[str]:
    """
    List workspaces sorted by most recently used.
    Uses thread_registry.json timestamps to sort (newest first).
    Falls back to alphabetical if no registry entry exists.
    """
    import json
    from agents_utils.config import PROJECT_ROOT
    
    root = Path(workspace_root)
    if not root.exists():
        return []
    
    # Get all directories
    dirs = [d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not dirs:
        return []
    
    # Try to load registry for timestamps
    registry_path = PROJECT_ROOT / "thread_registry.json"
    registry = {}
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    
    # Build (project_name, last_used_timestamp) pairs
    # Use 0 as default for projects not in registry
    projects_with_time = []
    for name in dirs:
        # Find the thread_ts that has this project_name
        last_used = 0.0
        for thread_ts, data in registry.items():
            if data.get("project_name") == name:
                try:
                    last_used = float(thread_ts)
                except ValueError:
                    pass
                break  # Use first match (most recent by thread_ts key)
        projects_with_time.append((name, last_used))
    
    # Sort by timestamp descending (newest first)
    projects_with_time.sort(key=lambda x: x[1], reverse=True)
    
    return [name for name, _ in projects_with_time]


def create_workspace(workspace_root: str, project_name: str) -> str:
    """
    Creates a new project workspace with standard subfolders.

    Structure:
        workspaces/
            project_name/
                research/           ← Researcher saves findings here
                src/                ← Future: file uploads
                email_attachments/  ← Files to attach to emails
                email_drafts/       ← Saved email drafts
    """
    safe_name = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in project_name.strip()
    ).lower()

    workspace_path = Path(workspace_root) / safe_name

    if workspace_path.exists():
        logger.warning(f"[memory] Workspace already exists: {workspace_path}")
        return str(workspace_path)

    try:
        workspace_path.mkdir(parents=True)
        (workspace_path / "research").mkdir()
        (workspace_path / "src").mkdir()
        # Email media
        email_media = workspace_path / "email_media"
        email_media.mkdir()
        (email_media / "attachments").mkdir()
        (email_media / "drafts").mkdir()
        # LinkedIn media
        linkedin_media = workspace_path / "linkedin_media"
        linkedin_media.mkdir()
        (linkedin_media / "post_attachments").mkdir()
        (linkedin_media / "post_drafts").mkdir()
        logger.info(f"[memory] Created workspace: {workspace_path}")
        return str(workspace_path)
    except OSError as e:
        logger.error(f"[memory] Could not create workspace: {e}")
        raise
