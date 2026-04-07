"""
Gemma Swarm — Calendar Agent
==============================
Handles Google Calendar: list events (with date range), next event, create, delete.
All API calls delegated to tools/calendar_api.py.

Model: gemma-3-4b-it
Write actions (create, delete) route to human_gate for approval.
Read actions (list, next) go straight back to supervisor.
"""

import json
import logging
import re

from langchain_core.messages import HumanMessage

from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from agents_utils.config import LABEL

logger = logging.getLogger(__name__)

WRITE_ACTIONS = {"calendar_create", "calendar_delete"}
READ_ACTIONS  = {"calendar_list", "calendar_next"}


class CalendarAgent(BaseAgent):

    def __init__(self):
        super().__init__("calendar_agent")

    def get_system_prompt(self) -> str:
        from system_prompts.calendar_agent_prompt import get_prompt
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

        logger.error(f"[calendar_agent] Could not parse action: {response_text[:200]}")
        return None


def _format_event(e: dict) -> str:
    """Format a single event for display. Includes all fields + ID for deletion reference."""
    lines = [f"*{e['title']}*"]
    lines.append(f"📅 {e['start']} → {e['end']}")
    if e.get("location"):
        lines.append(f"📍 {e['location']}")
    if e.get("description"):
        lines.append(f"📝 {e['description'][:300]}")
    if e.get("link"):
        lines.append(f"🔗 <{e['link']}|Open in Calendar>")
    lines.append(f"ID: `{e['id']}`")
    return "\n".join(lines)


def _execute_action(action: str, params: dict, state: AgentState, slack_post_fn) -> tuple[bool, str]:
    from tools.calendar_api import (
        calendar_list_events,
        calendar_get_next_event,
        calendar_create_event,
        calendar_delete_event,
    )

    try:
        if action == "calendar_list":
            events = calendar_list_events(
                max_results=params.get("max_results", 10),
                start_date=params.get("start_date") or None,
                end_date=params.get("end_date") or None,
                slack_post_fn=slack_post_fn,
            )
            if not events:
                return True, "No events found in the specified date range."
            formatted = "\n\n".join(_format_event(e) for e in events)
            return True, formatted

        elif action == "calendar_next":
            event = calendar_get_next_event(slack_post_fn=slack_post_fn)
            if not event:
                return True, "No upcoming events found on your calendar."
            return True, _format_event(event)

        elif action == "calendar_create":
            event = calendar_create_event(
                title=params.get("title", "New Event"),
                start_datetime=params.get("start_datetime", ""),
                end_datetime=params.get("end_datetime", ""),
                description=params.get("description", ""),
                location=params.get("location", ""),
                timezone=params.get("timezone", "UTC"),
                slack_post_fn=slack_post_fn,
            )
            result = (
                f"📅 *Calendar event created successfully:*\n"
                f"*{event['title']}*\n"
                f"📅 {event['start']} → {event['end']}\n"
                f"🔗 Open in Calendar: <{event['link']}>"
            )
            return True, result

        elif action == "calendar_delete":
            event_id = params.get("event_id", "")
            if not event_id:
                return False, (
                    "I need an event ID to delete an event. "
                    "Please ask me to list your events first so I can identify the correct one."
                )
            success = calendar_delete_event(event_id, slack_post_fn=slack_post_fn)
            result  = "✅ Calendar event deleted successfully." if success else "❌ Could not delete the event."
            return success, result

        else:
            return False, f"Unknown action: {action}"

    except Exception as e:
        logger.error(f"[calendar_agent] Action '{action}' failed: {e}")
        return False, f"❌ Calendar action failed: {e}"


# ── Singleton ──────────────────────────────────────────────────────────────────

_calendar_agent_instance = None

def get_calendar_agent() -> CalendarAgent:
    global _calendar_agent_instance
    if _calendar_agent_instance is None:
        _calendar_agent_instance = CalendarAgent()
    return _calendar_agent_instance


# ── LangGraph Node ─────────────────────────────────────────────────────────────

def calendar_agent_node(state: AgentState) -> dict:
    agent            = get_calendar_agent()
    current_subtask  = state.get("current_subtask", "")
    messages         = state.get("messages", [])
    calendar_history = list(state.get("calendar_history", []))
    slack_channel    = state.get("slack_channel", "")
    slack_thread     = state.get("slack_thread_ts", "")

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
                logger.error(f"[calendar_agent] Slack post failed: {e}")

    logger.info(f"[calendar_agent] Task: {current_subtask[:80]}")

    decision = agent.decide(task=current_subtask, messages=calendar_history)

    if not decision:
        error_msg = HumanMessage(
            content=f"{LABEL['calendar_agent']}\nFailed to determine Calendar action."
        )
        return {
            "active_agent":                 "calendar_agent",
            "next_node":                    "supervisor",
            "error_message":                "Calendar agent failed to parse action.",
            "google_requires_confirmation": False,
            "requires_confirmation":        False,
            "messages":                     messages + [error_msg],
            "calendar_history":             calendar_history,
        }

    action  = decision.get("action", "")
    params  = decision.get("params", {})
    requires_confirmation = action in WRITE_ACTIONS

    success, result_text = _execute_action(action, params, state, slack_post_fn)

    task_msg   = HumanMessage(content=f"{LABEL['supervisor']}\n{current_subtask}")
    result_msg = HumanMessage(content=f"{LABEL['calendar_agent']}\n{result_text}")
    calendar_history.extend([task_msg, result_msg])

    return {
        "active_agent":                 "calendar_agent",
        "next_node":                    "supervisor",
        "google_requires_confirmation": requires_confirmation,
        "requires_confirmation":        requires_confirmation,
        "calendar_history":             calendar_history,
        "messages":                     messages + [result_msg],
    }
