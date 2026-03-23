"""
Gemma Swarm — Supervisor Agent
================================
The orchestrator. Two modes depending on whether a task plan exists:

PLANNED mode (is_complex_task=True):
  - Reads task_plan from state
  - Finds next pending subtask
  - Routes to correct agent
  - Marks subtasks complete as they return
  - When all done → routes to output_formatter

SIMPLE mode (is_complex_task=False):
  - Standard single-step routing
  - Decides agent based on user message
  - Routes to output_formatter when done

Model: gemma-3-27b-it (128k context)
"""

import logging
from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from agents_utils.config import LABEL
from slack_utils.handlers_workspace import get_user_preferences_prompt

logger = logging.getLogger(__name__)


class SupervisorAgent(BaseAgent):

    def __init__(self):
        super().__init__("supervisor")

    def get_system_prompt(self) -> str:
        from system_prompts.supervisor_prompt import get_prompt
        base_prompt = get_prompt()
        
        # Inject global user preferences if available
        prefs_prompt = get_user_preferences_prompt()
        if prefs_prompt:
            base_prompt += "\n\n" + prefs_prompt
        
        return base_prompt

    def think(self, messages: list) -> tuple[str, dict]:
        response_text, parsed = self.run(
            messages=messages,
        )

        if parsed is None:
            logger.warning("[supervisor] Could not parse response — defaulting to output_formatter.")
            parsed = {
                "response":               response_text,
                "current_subtask":        "",
                "requires_research":      False,
                "requires_deep_research": False,
                "requires_email":         False,
                "requires_confirmation":  False,
                "task_complete":          True,
                "next_node":              "output_formatter",
            }

        # Safety guards
        if parsed.get("next_node") == "human_gate" and not parsed.get("requires_confirmation"):
            logger.warning("[supervisor] human_gate without requires_confirmation — overriding.")
            parsed["next_node"]     = "output_formatter"
            parsed["task_complete"] = True

        if parsed.get("next_node") == "researcher" and not parsed.get("requires_research"):
            logger.warning("[supervisor] researcher without requires_research — overriding.")
            parsed["next_node"]     = "output_formatter"
            parsed["task_complete"] = True

        if parsed.get("next_node") == "deep_researcher" and not parsed.get("requires_deep_research"):
            logger.warning("[supervisor] deep_researcher without requires_deep_research — overriding.")
            parsed["next_node"]     = "output_formatter"
            parsed["task_complete"] = True

        if parsed.get("next_node") == "email_composer" and not parsed.get("requires_email"):
            logger.warning("[supervisor] email_composer without requires_email — overriding.")
            parsed["next_node"]     = "output_formatter"
            parsed["task_complete"] = True

        if parsed.get("next_node") == "linkedin_composer" and not parsed.get("requires_linkedin"):
            logger.warning("[supervisor] linkedin_composer without requires_linkedin — overriding.")
            parsed["next_node"]     = "output_formatter"
            parsed["task_complete"] = True

        return parsed.get("response", response_text), parsed


# ── LangGraph Node ─────────────────────────────────────────────────────────────

_supervisor_agent = None

def get_supervisor_agent() -> SupervisorAgent:
    global _supervisor_agent
    if _supervisor_agent is None:
        _supervisor_agent = SupervisorAgent()
    return _supervisor_agent


def _mark_subtask_done(task_plan: list, current_subtask: str) -> list:
    """Mark the most recently completed subtask as done."""
    # Find first pending subtask whose description matches current_subtask
    updated = []
    marked  = False
    for s in task_plan:
        if not marked and s["status"] == "pending" and (
            current_subtask.lower() in s["description"].lower() or
            s["description"].lower() in current_subtask.lower()
        ):
            updated.append({**s, "status": "done"})
            marked = True
        else:
            updated.append(s)
    # If no match found, mark the first pending one as done
    if not marked:
        for i, s in enumerate(updated):
            if s["status"] == "pending":
                updated[i] = {**s, "status": "done"}
                break
    return updated


def supervisor_agent_node(state: AgentState) -> dict:
    agent = get_supervisor_agent()

    messages        = state.get("messages", [])
    retry_counts    = state.get("retry_counts", {})
    original_task   = state.get("original_task", "")
    task_plan       = state.get("task_plan", [])
    is_complex_task = state.get("is_complex_task", False)
    current_subtask = state.get("current_subtask", "")
    
    from langchain_core.messages import HumanMessage
    
    # Mark previous subtask as done if we're returning from an agent
    active_agent = state.get("active_agent", "")
    if active_agent in ("researcher", "deep_researcher", "email_composer", "email_send", "linkedin_composer", "linkedin_send") and task_plan:
        task_plan = _mark_subtask_done(task_plan, current_subtask)

    logger.info(f"[supervisor] Thinking... (complex={is_complex_task}, plan_items={len(task_plan)})")

    response_text, parsed = agent.think(
        messages=messages,
    )

    new_subtask            = parsed.get("current_subtask", "")
    requires_research      = parsed.get("requires_research", False)
    requires_deep_research = parsed.get("requires_deep_research", False)
    requires_email          = parsed.get("requires_email", False)
    requires_linkedin       = parsed.get("requires_linkedin", False)
    requires_confirmation  = parsed.get("requires_confirmation", False)
    task_complete          = parsed.get("task_complete", False)
    next_node              = parsed.get("next_node", "output_formatter")

    if not original_task and messages:
        last_human = next(
            (m.content for m in reversed(messages) if isinstance(m, HumanMessage)),
            ""
        )
        original_task = last_human

    if new_subtask != current_subtask:
        retry_counts = {}

    logger.info(
        f"[supervisor] Decision: next={next_node}, "
        f"research={requires_research}, deep={requires_deep_research}, "
        f"email={requires_email}, complete={task_complete}"
    )

    return {
        "messages": messages + [
            HumanMessage(content=f"{LABEL['supervisor']}\n{response_text}")
        ],
        "original_task":           original_task,
        "current_subtask":         new_subtask,
        "task_plan":               task_plan,
        "requires_research":       requires_research,
        "requires_deep_research":  requires_deep_research,
        "requires_email":          requires_email,
        "requires_linkedin":       requires_linkedin,
        "requires_confirmation":   requires_confirmation,
        "task_complete":           task_complete,
        "next_node":               next_node,
        "active_agent":            "supervisor",
        "retry_counts":            retry_counts,
    }
