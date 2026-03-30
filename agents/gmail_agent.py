"""
Gemma Swarm — Gmail Agent
===========================
Handles Gmail: list inbox, read full email, check+read from sender, watch/unwatch.
All API calls delegated to tools/gmail_api.py and tools/email_watch.py.

Model: gemma-3-4b-it
All Gmail actions are read-only — no human gate needed.
"""

import json
import logging
import re

from langchain_core.messages import HumanMessage

from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from agents_utils.config import LABEL

logger = logging.getLogger(__name__)


class GmailAgent(BaseAgent):

    def __init__(self):
        super().__init__("gmail_agent")

    def get_system_prompt(self) -> str:
        from system_prompts.gmail_agent_prompt import get_prompt
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

        logger.error(f"[gmail_agent] Could not parse action: {response_text[:200]}")
        return None


def _execute_action(action: str, params: dict, state: AgentState, slack_post_fn) -> tuple[bool, str]:
    from tools.gmail_api import (
        gmail_list_messages,
        gmail_read_message,
        gmail_check_for_sender,
    )
    from tools.email_watch import (
        start_email_watch,
        stop_email_watch,
        list_active_watches,
    )
    from agents_utils.graph import _slack_client

    slack_client = _slack_client
    channel      = state.get("slack_channel", "")
    thread_ts    = state.get("slack_thread_ts", "")

    try:
        if action == "gmail_list":
            msgs = gmail_list_messages(
                max_results=params.get("max_results", 5),
                query=params.get("query", ""),
                slack_post_fn=slack_post_fn,
            )
            if not msgs:
                return True, "No emails found matching your request."
            lines = []
            for m in msgs:
                lines.append(
                    f"• *From:* {m['from']} | *Subject:* {m['subject']} | "
                    f"*Date:* {m['date']} | ID: `{m['id']}`"
                )
            return True, "\n".join(lines)

        elif action == "gmail_read":
            message_id = params.get("message_id", "")
            if not message_id:
                return False, "No message ID provided. Please list emails first then ask to read a specific one."
            msg = gmail_read_message(message_id, slack_post_fn=slack_post_fn)
            if not msg:
                return False, "Could not fetch the email. The message ID may be invalid."
            result = (
                f"*From:* {msg['from']}\n"
                f"*Subject:* {msg['subject']}\n"
                f"*Date:* {msg['date']}\n"
                f"{'─' * 40}\n"
                f"{msg['body'] or '_(no plain text content found)_'}"
            )
            return True, result

        elif action == "gmail_check_sender":
            sender  = params.get("sender_email", "")
            message = gmail_check_for_sender(sender, slack_post_fn=slack_post_fn)
            if message:
                result = (
                    f"✅ Email from *{sender}* found!\n"
                    f"*Subject:* {message['subject']}\n"
                    f"*Date:* {message['date']}\n"
                    f"{'─' * 40}\n"
                    f"{message['body'] or '_(no plain text content found)_'}"
                )
            else:
                result = f"No unread email from *{sender}* found in your inbox."
            return True, result

        elif action == "gmail_watch_start":
            sender = params.get("sender_email", "")
            if not sender:
                return False, "No sender email specified for watch."
            if not slack_client:
                return False, "Slack client not available for watch notifications."
            started = start_email_watch(
                sender_email=sender,
                slack_client=slack_client,
                channel=channel,
                thread_ts=thread_ts,
            )
            if started:
                result = (
                    f"👀 Now watching for an email from *{sender}*.\n"
                    "I'll notify you in this thread as soon as it arrives (checking every 5 minutes).\n"
                    "You can continue chating in the meantime."
                )
            else:
                result = f"ℹ️ Already watching for an email from *{sender}*."
            return True, result

        elif action == "gmail_watch_stop":
            sender  = params.get("sender_email", "")
            stopped = stop_email_watch(sender)
            result  = (
                f"✅ Stopped watching for email from *{sender}*."
                if stopped else
                f"No active watch found for *{sender}*."
            )
            return True, result

        elif action == "gmail_watch_list":
            watches = list_active_watches()
            if watches:
                result = "👀 Currently watching for emails from:\n" + "\n".join(f"• {s}" for s in watches)
            else:
                result = "No active email watches."
            return True, result

        else:
            return False, f"Unknown action: {action}"

    except Exception as e:
        logger.error(f"[gmail_agent] Action '{action}' failed: {e}")
        return False, f"❌ Gmail action failed: {e}"


# ── Singleton ──────────────────────────────────────────────────────────────────

_gmail_agent_instance = None

def get_gmail_agent() -> GmailAgent:
    global _gmail_agent_instance
    if _gmail_agent_instance is None:
        _gmail_agent_instance = GmailAgent()
    return _gmail_agent_instance


# ── LangGraph Node ─────────────────────────────────────────────────────────────

def gmail_agent_node(state: AgentState) -> dict:
    agent           = get_gmail_agent()
    current_subtask = state.get("current_subtask", "")
    messages        = state.get("messages", [])
    gmail_history   = list(state.get("gmail_history", []))
    slack_channel   = state.get("slack_channel", "")
    slack_thread    = state.get("slack_thread_ts", "")

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
                logger.error(f"[gmail_agent] Slack post failed: {e}")

    logger.info(f"[gmail_agent] Task: {current_subtask[:80]}")

    decision = agent.decide(task=current_subtask, messages=gmail_history)

    if not decision:
        error_msg = HumanMessage(content=f"{LABEL['gmail_agent']}\nFailed to determine Gmail action.")
        return {
            "active_agent":                 "gmail_agent",
            "next_node":                    "supervisor",
            "error_message":                "Gmail agent failed to parse action.",
            "google_requires_confirmation": False,
            "requires_confirmation":        False,
            "messages":                     messages + [error_msg],
            "gmail_history":                gmail_history,
        }

    action  = decision.get("action", "")
    params  = decision.get("params", {})
    success, result_text = _execute_action(action, params, state, slack_post_fn)

    task_msg   = HumanMessage(content=f"{LABEL['supervisor']}\n{current_subtask}")
    result_msg = HumanMessage(content=f"{LABEL['gmail_agent']}\n{result_text}")
    gmail_history.extend([task_msg, result_msg])

    return {
        "active_agent":                 "gmail_agent",
        "next_node":                    "supervisor",
        "google_requires_confirmation": False,
        "requires_confirmation":        False,
        "gmail_history":                gmail_history,
        "messages":                     messages + [result_msg],
    }
