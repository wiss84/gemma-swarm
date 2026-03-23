"""
Gemma Swarm — Task Classifier
================================
Lightweight 1b model. Single job: decide if a user message needs
multi-step planning or can be handled directly by the supervisor.

Only receives the latest user message — no conversation history.
Biases toward simple (false) when uncertain.
"""

import re
import json
import logging
from langchain_core.messages import HumanMessage
from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from agents_utils.config import LABEL

logger = logging.getLogger(__name__)


class TaskClassifierAgent(BaseAgent):

    def __init__(self):
        super().__init__("task_classifier")

    def get_system_prompt(self) -> str:
        from system_prompts.task_classifier_prompt import get_prompt
        return get_prompt()

    def classify(self, user_message: str) -> bool:
        """Returns True if task is complex, False if simple."""
        response_text, parsed = self.run(
            messages=[HumanMessage(content=user_message)]
        )

        if parsed and "complex" in parsed:
            return bool(parsed["complex"])

        # Try manual extraction
        match = re.search(r'"complex"\s*:\s*(true|false)', response_text.lower())
        if match:
            return match.group(1) == "true"

        # Default to simple when uncertain
        logger.warning("[task_classifier] Could not parse response — defaulting to simple.")
        return False


# ── LangGraph Node ─────────────────────────────────────────────────────────────

_task_classifier_agent = None

def get_task_classifier_agent() -> TaskClassifierAgent:
    global _task_classifier_agent
    if _task_classifier_agent is None:
        _task_classifier_agent = TaskClassifierAgent()
    return _task_classifier_agent


def task_classifier_node(state: AgentState) -> dict:
    """
    Reads only the latest human message.
    Sets is_complex_task in state.
    Routes to planner (complex) or supervisor (simple).
    """
    agent    = get_task_classifier_agent()
    messages = state.get("messages", [])

    # Extract only the latest human message
    latest_message = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content.startswith(LABEL["human"]):
                latest_message = content.replace(LABEL["human"], "").strip()
                break

    if not latest_message:
        logger.warning("[task_classifier] No human message found — defaulting to simple.")
        return {
            "is_complex_task": False,
            "next_node":       "supervisor",
        }

    is_complex = agent.classify(latest_message)

    logger.info(f"[task_classifier] Message classified as: {'complex' if is_complex else 'simple'}")

    return {
        "is_complex_task": is_complex,
        "next_node":       "planner" if is_complex else "supervisor",
    }
