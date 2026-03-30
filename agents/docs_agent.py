"""
Gemma Swarm — Docs Agent
==========================
Handles Google Docs: create, read, update.
All API calls delegated to tools/docs_api.py.

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

WRITE_ACTIONS = {"docs_create", "docs_update"}
READ_ACTIONS  = {"docs_read"}


def _extract_id_from_url(value: str) -> str:
    match = re.search(r"/d/([a-zA-Z0-9_-]{20,})", value)
    return match.group(1) if match else value


class DocsAgent(BaseAgent):

    def __init__(self):
        super().__init__("docs_agent")

    def get_system_prompt(self) -> str:
        from system_prompts.docs_agent_prompt import get_prompt
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

        logger.error(f"[docs_agent] Could not parse action: {response_text[:200]}")
        return None


def _execute_action(action: str, params: dict, state: AgentState, slack_post_fn) -> tuple[bool, str]:
    from tools.docs_api import docs_create, docs_read, docs_update

    try:
        if action == "docs_create":
            doc    = docs_create(
                title=params.get("title", "Untitled Document"),
                content=params.get("content", ""),
                slack_post_fn=slack_post_fn,
            )
            result = (
                f"📄 *Google Doc ready for confirmation:*\n"
                f"*{doc['title']}*\n"
                f"🔗 <{doc['link']}|Open in Google Docs>"
            )
            return True, result

        elif action == "docs_read":
            doc_id = _extract_id_from_url(params.get("doc_id", ""))
            doc    = docs_read(doc_id, slack_post_fn=slack_post_fn)
            result = (
                f"📄 *{doc['title']}*\n\n"
                f"{doc['content'][:3000]}"
                + ("\n\n_(content truncated)_" if len(doc["content"]) > 3000 else "")
                + f"\n\n🔗 <{doc['link']}|Open in Google Docs>"
            )
            return True, result

        elif action == "docs_update":
            doc_id = _extract_id_from_url(params.get("doc_id", ""))
            doc    = docs_update(
                doc_id=doc_id,
                new_content=params.get("new_content", ""),
                slack_post_fn=slack_post_fn,
            )
            result = (
                f"📄 *Google Doc updated and ready for confirmation:*\n"
                f"🔗 <{doc['link']}|Open in Google Docs>"
            )
            return True, result

        else:
            return False, f"Unknown action: {action}"

    except Exception as e:
        logger.error(f"[docs_agent] Action '{action}' failed: {e}")
        return False, f"❌ Docs action failed: {e}"


# ── Singleton ──────────────────────────────────────────────────────────────────

_docs_agent_instance = None

def get_docs_agent() -> DocsAgent:
    global _docs_agent_instance
    if _docs_agent_instance is None:
        _docs_agent_instance = DocsAgent()
    return _docs_agent_instance


# ── LangGraph Node ─────────────────────────────────────────────────────────────

def docs_agent_node(state: AgentState) -> dict:
    agent           = get_docs_agent()
    current_subtask = state.get("current_subtask", "")
    messages        = state.get("messages", [])
    docs_history    = list(state.get("docs_history", []))
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
                logger.error(f"[docs_agent] Slack post failed: {e}")

    logger.info(f"[docs_agent] Task: {current_subtask[:80]}")

    decision = agent.decide(task=current_subtask, messages=docs_history)

    if not decision:
        error_msg = HumanMessage(
            content=f"{LABEL['docs_agent']}\nFailed to determine Docs action."
        )
        return {
            "active_agent":                 "docs_agent",
            "next_node":                    "supervisor",
            "error_message":                "Docs agent failed to parse action.",
            "google_requires_confirmation": False,
            "requires_confirmation":        False,
            "messages":                     messages + [error_msg],
            "docs_history":                 docs_history,
        }

    action  = decision.get("action", "")
    params  = decision.get("params", {})
    requires_confirmation = action in WRITE_ACTIONS

    success, result_text = _execute_action(action, params, state, slack_post_fn)

    task_msg   = HumanMessage(content=f"{LABEL['supervisor']}\n{current_subtask}")
    result_msg = HumanMessage(content=f"{LABEL['docs_agent']}\n{result_text}")
    docs_history.extend([task_msg, result_msg])

    return {
        "active_agent":                 "docs_agent",
        "next_node":                    "supervisor",
        "google_requires_confirmation": requires_confirmation,
        "requires_confirmation":        requires_confirmation,
        "docs_history":                 docs_history,
        "messages":                     messages + [result_msg],
    }
