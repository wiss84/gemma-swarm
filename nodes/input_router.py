"""
Gemma Swarm — Input Router Node
==================================
Deterministic node. No LLM call.
First node after a message arrives from Slack.

Responsibilities:
- Strip Slack URL formatting <url|text> → plain url
- Update original_task on EVERY new message (not just first)
- Reset planning state for each new message
- Load task notes if workspace is set
- Route to guard_rails
"""

import logging
from langchain_core.messages import HumanMessage
from agents_utils.state import AgentState
from agents_utils.memory import estimate_messages_tokens
from agents_utils.config import CONTEXT_SUMMARIZE_THRESHOLD, MODEL_CONTEXT_WINDOWS, MODELS

logger = logging.getLogger(__name__)


def input_router_node(state: AgentState) -> dict:
    messages       = state.get("messages", [])
    workspace_path = state.get("workspace_path", "")

    # Get the latest human message (no label prefix in new design)
    latest_human = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            # Skip non-human messages stored as HumanMessage (old supervisor responses)
            # In new design: supervisor responses are also HumanMessage but come AFTER the user msg
            # The user's message is the last one added before this node runs
            # so the very last HumanMessage in the list IS the user's message
            latest_human = content.strip()
            break

    if not latest_human:
        logger.warning("[input_router] No human message found.")
        return {"next_node": "guard_rails"}

    logger.info(f"[input_router] Routing: {latest_human[:80]}")

    original_task = latest_human

    # ── Check if memory compression needed before starting new task ───────────
    compression_needed = False

    supervisor_limit  = MODEL_CONTEXT_WINDOWS[MODELS["supervisor"]]
    supervisor_tokens = estimate_messages_tokens(messages)
    logger.info(f"[input_router] Supervisor context: {supervisor_tokens}/{supervisor_limit} ({supervisor_tokens/supervisor_limit:.1%})")
    if (supervisor_tokens / supervisor_limit) >= CONTEXT_SUMMARIZE_THRESHOLD:
        compression_needed = True
        logger.info("[input_router] Supervisor needs compression.")

    if compression_needed:
        logger.info("[input_router] Routing to memory for compression.")

    next_node = "memory" if compression_needed else "guard_rails"

    return {
        "original_task":  original_task,
        "next_node":      next_node,
        # Reset per-run state
        "task_complete":  False,
        "error_message": "",
        "human_decision": "",
        "email_draft":    {},
        "active_agent":   "",
        "retry_counts":   {},
        "loaded_toolset": "",
    }
