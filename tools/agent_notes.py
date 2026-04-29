"""
Gemma Swarm — Agent Notes Tools
================================
Tools for the coding agent to read and write learning notes across sessions.

These tools enable the agent to record insights, mistakes, and patterns discovered
during coding sessions. Notes are persisted to agent_notes.md and automatically
loaded at the start of future sessions (when agent_notes_enabled is True).

Tools:
    read_agent_notes()               — Read all past learning notes
    write_agent_note(note: str)       — Append a new learning note with timestamp
"""

import logging
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from agents_utils.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

AGENT_NOTES_FILE = PROJECT_ROOT / "coding_agent" / "agent_notes.md"


class WriteAgentNoteInput(BaseModel):
    note: str = Field(
        description="A concise learning note: a mistake, insight, pattern, or tip for future sessions. "
                    "Be specific and actionable. Example: 'When using pytest, always use tmp_path fixture "
                    "instead of temporary file paths — it handles cleanup automatically.'"
    )


@tool(args_schema=WriteAgentNoteInput)
def write_agent_note(note: str) -> str:
    """
    Append a learning note to the agent's persistent memory file.
    
    Use this to record:
    - Mistakes made and how they were fixed
    - Discovered patterns or conventions in the codebase
    - Tips for similar future tasks
    - Quirks specific to this project/environment
    
    Notes are timestamped and loaded automatically in future sessions
    when agent_notes_enabled is True.
    """
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        AGENT_NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AGENT_NOTES_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n## {timestamp}\n{note.strip()}\n")
        logger.info(f"[agent_notes] Appended note ({len(note)} chars)")
        return "Note saved."
    except Exception as e:
        logger.error(f"[agent_notes] Failed to write note: {e}")
        return f"Failed to save note: {e}"


@tool
def read_agent_notes() -> str:
    """
    Read all past learning notes from previous sessions.
    
    Call this when you're unsure how to handle a situation you've encountered before,
    or when starting a complex task to check for relevant past experiences.
    Notes are organized by timestamp with the most recent at the bottom.
    """
    try:
        if not AGENT_NOTES_FILE.exists():
            return "No agent notes file found — no past experiences recorded."
        content = AGENT_NOTES_FILE.read_text(encoding="utf-8").strip()
        if not content:
            return "Agent notes file is empty — no past experiences recorded."
        return content
    except Exception as e:
        logger.error(f"[agent_notes] Failed to read notes: {e}")
        return f"Failed to read notes: {e}"
