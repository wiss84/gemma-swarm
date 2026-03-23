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
from agents_utils.config import LABEL, CONTEXT_SUMMARIZE_THRESHOLD, MODEL_CONTEXT_WINDOWS, MODELS

logger = logging.getLogger(__name__)


def input_router_node(state: AgentState) -> dict:
    messages       = state.get("messages", [])
    workspace_path = state.get("workspace_path", "")

    # Get the latest human message — labeled with LABEL["human"] by slack_bot
    latest_human = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content.startswith(LABEL["human"]):
                latest_human = content.replace(LABEL["human"], "").strip()
                break

    if not latest_human:
        logger.warning("[input_router] No human message found.")
        return {"next_node": "guard_rails"}

    logger.info(f"[input_router] Routing: {latest_human[:80]}")


    # ALWAYS update original_task to latest human message
    # Prevents validator from checking against stale tasks like "hi"
    original_task = latest_human

    # ── Check if memory compression needed before starting new task ───────────
    # Check supervisor + all 4 agent histories in one pass
    compression_needed = False

    # 1. Supervisor messages
    supervisor_limit = MODEL_CONTEXT_WINDOWS[MODELS["supervisor"]]
    supervisor_tokens = estimate_messages_tokens(messages)
    logger.info(f"[input_router] Supervisor context: {supervisor_tokens}/{supervisor_limit} ({supervisor_tokens/supervisor_limit:.1%})")
    if (supervisor_tokens / supervisor_limit) >= CONTEXT_SUMMARIZE_THRESHOLD:
        compression_needed = True
        logger.info("[input_router] Supervisor needs compression.")

    # 2. Agent histories
    agent_history_checks = [
        ("researcher",        "researcher_history",        MODELS["researcher"]),
        ("deep_researcher",   "deep_researcher_history",   MODELS["deep_researcher"]),
        ("email_composer",    "email_history",             MODELS["email_composer"]),
        ("linkedin_composer", "linkedin_history",          MODELS["linkedin_composer"]),
    ]
    for agent_name, history_field, model_name in agent_history_checks:
        history = state.get(history_field, [])
        if not history:
            continue
        agent_limit  = MODEL_CONTEXT_WINDOWS.get(model_name, 128000)
        agent_tokens = estimate_messages_tokens(history)
        logger.info(f"[input_router] {agent_name} history: {agent_tokens}/{agent_limit} ({agent_tokens/agent_limit:.1%})")
        if (agent_tokens / agent_limit) >= CONTEXT_SUMMARIZE_THRESHOLD:
            compression_needed = True
            logger.info(f"[input_router] {agent_name} history needs compression.")

    if compression_needed:
        logger.info("[input_router] Routing to memory for compression.")

    next_node = "memory" if compression_needed else "guard_rails"

    return {
        "original_task":     original_task,
        "next_node":         next_node,
        # Reset per-run state
        "task_complete":     False,
        "error_message":     "",
        "is_complex_task":   False,
        "task_plan":         [],
        "current_subtask":   "",
        "subtask_results":   {},
        "human_decision":    "",
        "email_draft":       {},
        "active_agent":      "",
        "retry_counts":      {},
    }
