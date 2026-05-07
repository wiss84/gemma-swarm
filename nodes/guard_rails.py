"""
Gemma Swarm — Guard Rails Node
=================================
Deterministic node. No LLM call.
Runs after input router, before supervisor.

Responsibilities:
- Block dangerous requests (rm -rf, format drive, etc.)
- Flag sensitive operations that need human confirmation
- Validate workspace path is set for file/code tasks
- Prevent prompt injection attempts
"""

import logging
import re
from langchain_core.messages import HumanMessage
from agents_utils.state import AgentState
from agents_utils.config import BLOCKED_PATTERNS

logger = logging.getLogger(__name__)

# Prompt injection patterns to detect
INJECTION_PATTERNS = [
    r"ignore (previous|all|above) instructions",
    r"you are now",
    r"new persona",
    r"forget (everything|all|your instructions)",
    r"disregard (your|all|previous)",
    r"system prompt",
    r"act as (a|an)(?! supervisor|researcher|coder|executor)",
]


def _check_blocked(text: str) -> str | None:
    """
    Check if text contains any blocked patterns.
    Returns the matched pattern string if found, None if clean.
    """
    text_lower = text.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern.lower() in text_lower:
            return pattern
    return None


def _check_injection(text: str) -> bool:
    """
    Check for prompt injection attempts.
    Returns True if injection detected.
    """
    text_lower = text.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def guard_rails_node(state: AgentState) -> dict:
    """
    Deterministic guard rails.
    Blocks dangerous requests before any agent processes them.
    Returns blocked response directly without hitting supervisor.
    """
    messages = state.get("messages", [])

    # Get latest human message (no label prefix in new design)
    latest_human = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            latest_human = content.strip()
            break

    if not latest_human:
        return {"next_node": "supervisor"}

    # Check for blocked patterns
    blocked = _check_blocked(latest_human)
    if blocked:
        logger.warning(f"[guard_rails] Blocked pattern: {blocked}")
        return {
            "next_node":     "output_formatter",
            "task_complete": True,
            "formatted_output": [f"This request was blocked.\nReason: Contains potentially dangerous pattern: '{blocked}'\nPlease rephrase your request."],
        }

    if _check_injection(latest_human):
        logger.warning("[guard_rails] Prompt injection detected.")
        return {
            "next_node":     "output_formatter",
            "task_complete": True,
            "formatted_output": ["This request was blocked.\nReason: Potential prompt injection detected.\nPlease rephrase your request."],
        }

    logger.info("[guard_rails] Input passed all checks.")
    return {"next_node": "supervisor"}
