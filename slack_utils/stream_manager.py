"""
Gemma Swarm — Stream Manager (v2)
===================================
Manages live agent progress UI in Slack.

Approach:
  - assistant.threads.setStatus  → animated app-name indicator, cycles status text
  - chat_postMessage per card     → task_card blocks posted one by one as events happen
  - setStatus clears automatically when the final answer is posted

No chat.startStream / appendStream / stopStream used.

Public API:
  sm = StreamManager(client, channel, thread_ts, user_id="")
  sm.open()                                       — posts initial setStatus
  sm.push_thinking(text)                          — posts thinking task_card
  sm.push_tool_end(id, name, input, output, err)  — posts tool task_card
  sm.next_tool_id()                               — returns unique task_id string
  sm.close()                                      — clears setStatus (optional, auto-clears on final post)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ── Tool emoji map ─────────────────────────────────────────────────────────────

_TOOL_EMOJI: dict[str, str] = {
    # Supervisor meta
    "load_toolset":                  "🔌",
    # Gmail
    "gmail_list_messages":           "📧",
    "gmail_read_message":            "📧",
    "gmail_check_for_sender":        "🔍",
    "send_email":                    "📤",
    # Email watch
    "email_watch_start":             "👁️",
    "email_watch_stop":              "👁️",
    "email_watch_list":              "👁️",
    # Calendar
    "calendar_list":                 "📅",
    "calendar_next":                 "📅",
    "calendar_create":               "📅",
    "calendar_delete":               "🗑️",
    # Docs
    "docs_read":                     "📄",
    "docs_create":                   "📄",
    "docs_update":                   "📄",
    # Sheets
    "sheets_read":                   "📊",
    "sheets_create":                 "📊",
    "sheets_update":                 "📊",
    # Research
    "search_web":                    "🌐",
    "fetch_page":                    "🌐",
    "fetch_next_chunk":              "🌐",
    # LinkedIn
    "publish_linkedin_post":         "💼",
    # Coding — workspace
    "read_files":                    "📂",
    "write_files":                   "💾",
    "edit_files":                    "✏️",
    "execute_shell":                 "⚡",
    # Coding — knowledge
    "get_installed_package_info":    "📦",
    "get_package_latest_version":    "📦",
    "fetch_package_docs":            "📖",
    "search_web":                    "🌐",
    "fetch_page":                    "🌐",
    "fetch_next_chunk":              "🌐",
    # Coding — project
    "read_project_structure":        "🗂️",
    "read_requirements":             "📋",
    "read_git_log":                  "📜",
    # Coding — validation
    "validate_files":                "✅",
    # Coding — git
    "git_commit":                    "💾",
    # Coding — environment
    "get_python_info":               "🐍",
    "get_env_variables":             "🔐",
    "install_package":               "📦",
    # Coding — semantic
    "find_references":               "🔍",
    "get_symbol_definition":         "🔍",
    "rename_symbol":                 "✏️",
    "analyze_module_dependencies":   "🕸️",
    # Coding — meta
    "spawn_subagent":                "🤖",
    "update_project_todo":           "📋",
    "read_agent_notes":              "🗒️",
    "write_agent_note":              "🗒️",
}

_DEFAULT_EMOJI = "🔧"


def _tool_title(tool_name: str) -> str:
    emoji    = _TOOL_EMOJI.get(tool_name, _DEFAULT_EMOJI)
    readable = tool_name.replace("_", " ").title()
    return f"{emoji} {readable}"


def _make_rich_text(text: str) -> dict:
    return {
        "type": "rich_text",
        "elements": [
            {
                "type": "rich_text_preformatted",
                "elements": [{"type": "text", "text": text}],
            }
        ],
    }


def _make_task_card_block(
    task_id: str,
    title: str,
    status: str,
    details: Optional[str] = None,
    output: Optional[str]  = None,
) -> dict:
    block: dict = {
        "type":    "task_card",
        "task_id": task_id,
        "title":   title,
        "status":  status,
    }
    if details:
        block["details"] = _make_rich_text(details.strip())
    if output:
        block["output"]  = _make_rich_text(output.strip())
    return block


# ── StreamManager ──────────────────────────────────────────────────────────────

class StreamManager:

    def __init__(self, client, channel: str, thread_ts: str, user_id: str = ""):
        self._client    = client
        self._channel   = channel
        self._thread_ts = thread_ts
        self._user_id   = user_id
        self._open      = False
        self._think_counter = 0
        self._tool_counter  = 0

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def open(self) -> bool:
        """Start the animated loading indicator."""
        try:
            self._client.assistant_threads_setStatus(
                channel_id=self._channel,
                thread_ts=self._thread_ts,
                status="🧠 Thinking...",
            )
            self._open = True
            logger.info("[stream_manager] Status set: thinking")
            return True
        except Exception as e:
            logger.warning(f"[stream_manager] open() failed: {e}")
            return False

    def close(self):
        """
        Clear the animated indicator.
        Normally auto-cleared when the final answer is posted —
        call this explicitly only on cancellation or error paths.
        """
        if not self._open:
            return
        try:
            # Posting an empty status clears the indicator
            self._client.assistant_threads_setStatus(
                channel_id=self._channel,
                thread_ts=self._thread_ts,
                status="",
            )
        except Exception as e:
            logger.debug(f"[stream_manager] close() failed: {e}")
        finally:
            self._open = False

    # ── Status cycling ─────────────────────────────────────────────────────────

    def set_status(self, text: str):
        """Update the animated indicator text."""
        if not self._open:
            return
        try:
            self._client.assistant_threads_setStatus(
                channel_id=self._channel,
                thread_ts=self._thread_ts,
                status=text,
            )
        except Exception as e:
            logger.debug(f"[stream_manager] set_status failed: {e}")

    # ── Event pushers ──────────────────────────────────────────────────────────

    def push_thinking(self, thinking_text: str):
        """Post a completed thinking task_card block."""
        if not thinking_text.strip():
            return
        self._think_counter += 1
        block = _make_task_card_block(
            task_id=f"think_{self._think_counter}",
            title=f"🧠 Thinking ({self._think_counter})",
            status="complete",
            details=thinking_text.strip(),
        )
        self._post_block(block)
        self.set_status("🧠 Thinking...")

    def push_tool_end(
        self,
        task_id: str,
        tool_name: str,
        tool_input: str,
        tool_output: str,
        error: bool = False,
    ):
        """Post a complete/error task_card block for a finished tool call."""
        block = _make_task_card_block(
            task_id=task_id,
            title=_tool_title(tool_name),
            status="error" if error else "complete",
            details=tool_input  or None,
            output=tool_output  or None,
        )
        self._post_block(block)
        self.set_status("🧠 Thinking...")

    def next_tool_id(self) -> str:
        self._tool_counter += 1
        return f"tool_{self._tool_counter}"

    # ── Internal ───────────────────────────────────────────────────────────────

    def _post_block(self, block: dict):
        fallback_text = block.get("title", "Agent update")
        try:
            self._client.chat_postMessage(
                channel=self._channel,
                thread_ts=self._thread_ts,
                text=fallback_text,
                blocks=[block],
            )
        except Exception as e:
            logger.warning(f"[stream_manager] _post_block failed: {e}")
