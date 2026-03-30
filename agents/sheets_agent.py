"""
Gemma Swarm — Sheets Agent
============================
Handles Google Sheets: create, read, update.
All API calls delegated to tools/sheets_api.py.

Model: gemma-3-4b-it
Write actions (create, update) route to human_gate for approval.
Read actions (read) go straight back to supervisor.
"""

import json
import logging
import re

from langchain_core.messages import HumanMessage

from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from agents_utils.config import LABEL

logger = logging.getLogger(__name__)

WRITE_ACTIONS = {"sheets_create", "sheets_update"}
READ_ACTIONS  = {"sheets_read"}


def _extract_id_from_url(value: str) -> str:
    match = re.search(r"/d/([a-zA-Z0-9_-]{20,})", value)
    return match.group(1) if match else value


class SheetsAgent(BaseAgent):

    def __init__(self):
        super().__init__("sheets_agent")

    def get_system_prompt(self) -> str:
        from system_prompts.sheets_agent_prompt import get_prompt
        return get_prompt()

    def decide(self, task: str, messages: list) -> dict | None:
        """Ask the LLM to pick an action and params. Returns {action, params} or None."""
        task_message  = HumanMessage(content=f"{LABEL['supervisor']}\n{task}")
        response_text, parsed = self.run(messages=messages + [task_message])

        if parsed and isinstance(parsed, dict) and "action" in parsed:
            return parsed

        match = re.search(r"\{[\s\S]+\}", response_text)
        if match:
            try:
                candidate = json.loads(match.group())
                if "action" in candidate:
                    return candidate
            except json.JSONDecodeError:
                pass

        logger.error(f"[sheets_agent] Could not parse action: {response_text[:200]}")
        return None


def _execute_action(action: str, params: dict, state: AgentState, slack_post_fn) -> tuple[bool, str]:
    from tools.sheets_api import sheets_create, sheets_read, sheets_update

    try:
        if action == "sheets_create":
            sheet  = sheets_create(
                title=params.get("title", "Untitled Spreadsheet"),
                rows=params.get("rows", []),
                slack_post_fn=slack_post_fn,
            )
            result = (
                f"📊 *Google Sheet ready for confirmation:*\n"
                f"*{sheet['title']}*\n"
                f"🔗 <{sheet['link']}|Open in Google Sheets>"
            )
            return True, result

        elif action == "sheets_read":
            sheet_id = _extract_id_from_url(params.get("sheet_id", ""))
            range_   = params.get("range", "Sheet1")
            sheet    = sheets_read(sheet_id, range_=range_, slack_post_fn=slack_post_fn)
            values   = sheet.get("values", [])
            if values:
                rows_text = "\n".join(
                    " | ".join(str(c) for c in row)
                    for row in values[:50]
                )
                if len(values) > 50:
                    rows_text += f"\n_(showing first 50 of {len(values)} rows)_"
            else:
                rows_text = "_(empty sheet)_"
            result = (
                f"📊 *{sheet['title']}*\n\n"
                f"{rows_text}\n\n"
                f"🔗 <{sheet['link']}|Open in Google Sheets>"
            )
            return True, result

        elif action == "sheets_update":
            sheet_id = _extract_id_from_url(params.get("sheet_id", ""))
            updated  = sheets_update(
                sheet_id=sheet_id,
                range_=params.get("range", "Sheet1!A1"),
                values=params.get("values", []),
                slack_post_fn=slack_post_fn,
            )
            result = (
                f"📊 *Google Sheet updated and ready for confirmation:*\n"
                f"🔗 <{updated['link']}|Open in Google Sheets>"
            )
            return True, result

        else:
            return False, f"Unknown action: {action}"

    except Exception as e:
        logger.error(f"[sheets_agent] Action '{action}' failed: {e}")
        return False, f"❌ Sheets action failed: {e}"


# ── Singleton ──────────────────────────────────────────────────────────────────

_sheets_agent_instance = None

def get_sheets_agent() -> SheetsAgent:
    global _sheets_agent_instance
    if _sheets_agent_instance is None:
        _sheets_agent_instance = SheetsAgent()
    return _sheets_agent_instance


# ── LangGraph Node ─────────────────────────────────────────────────────────────

def sheets_agent_node(state: AgentState) -> dict:
    agent          = get_sheets_agent()
    current_subtask = state.get("current_subtask", "")
    messages       = state.get("messages", [])
    sheets_history = list(state.get("sheets_history", []))
    slack_channel  = state.get("slack_channel", "")
    slack_thread   = state.get("slack_thread_ts", "")

    from agents_utils.graph import _slack_client
    slack_client = _slack_client

    def slack_post_fn(text: str):
        if slack_client and slack_channel:
            try:
                slack_client.chat_postMessage(
                    channel=slack_channel,
                    thread_ts=slack_thread,
                    text=text,
                    mrkdwn=True,
                )
            except Exception as e:
                logger.error(f"[sheets_agent] Slack post failed: {e}")

    logger.info(f"[sheets_agent] Task: {current_subtask[:80]}")

    decision = agent.decide(task=current_subtask, messages=sheets_history)

    if not decision:
        error_msg = HumanMessage(
            content=f"{LABEL['sheets_agent']}\nFailed to determine Sheets action."
        )
        return {
            "active_agent":                 "sheets_agent",
            "next_node":                    "supervisor",
            "error_message":                "Sheets agent failed to parse action.",
            "google_requires_confirmation": False,
            "requires_confirmation":        False,
            "messages":                     messages + [error_msg],
            "sheets_history":               sheets_history,
        }

    action  = decision.get("action", "")
    params  = decision.get("params", {})
    requires_confirmation = action in WRITE_ACTIONS

    success, result_text = _execute_action(action, params, state, slack_post_fn)

    task_msg   = HumanMessage(content=f"{LABEL['supervisor']}\n{current_subtask}")
    result_msg = HumanMessage(content=f"{LABEL['sheets_agent']}\n{result_text}")
    sheets_history.extend([task_msg, result_msg])

    return {
        "active_agent":                 "sheets_agent",
        "next_node":                    "supervisor",
        "google_requires_confirmation": requires_confirmation,
        "requires_confirmation":        requires_confirmation,
        "sheets_history":               sheets_history,
        "messages":                     messages + [result_msg],
    }
