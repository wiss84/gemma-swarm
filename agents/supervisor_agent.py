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
from agents_utils.config import LABEL, AGENT_GUARDS
from agents_utils.context_tracker import snapshot_context_usage
from agents_utils.context_ui_launcher import launch_context_ui
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
        for node, flag in AGENT_GUARDS.items():
            if parsed.get("next_node") == node and not parsed.get(flag):
                logger.warning(f"[supervisor] {node} without {flag} — overriding.")
                parsed["next_node"]     = "output_formatter"
                parsed["task_complete"] = True
                break

        return parsed.get("response", response_text), parsed


# ── LangGraph Node ─────────────────────────────────────────────────────────────

_supervisor_agent = None

def get_supervisor_agent() -> SupervisorAgent:
    global _supervisor_agent
    if _supervisor_agent is None:
        _supervisor_agent = SupervisorAgent()
    return _supervisor_agent


def _mark_subtask_done(task_plan: list, current_subtask_id: int | None) -> list:
    """Mark the subtask with the given ID as done."""
    if current_subtask_id is None:
        return task_plan

    updated = []
    marked  = False
    for s in task_plan:
        if not marked and s.get("id") == current_subtask_id and s["status"] == "pending":
            updated.append({**s, "status": "done"})
            marked = True
        else:
            updated.append(s)
    
    # If no match found by ID, mark the first pending one as done (fallback)
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
    current_subtask_id = state.get("current_subtask_id")
    if active_agent in ("researcher", "deep_researcher", "email_composer", "email_send", "linkedin_composer", "linkedin_send") and task_plan:
        task_plan = _mark_subtask_done(task_plan, current_subtask_id)

    logger.info(f"[supervisor] Thinking... (complex={is_complex_task}, plan_items={len(task_plan)})")

    response_text, parsed = agent.think(
        messages=messages,
    )

    new_subtask            = parsed.get("current_subtask", "")
    new_subtask_id            = parsed.get("current_subtask_id")
    requires_research      = parsed.get("requires_research", False)
    requires_deep_research = parsed.get("requires_deep_research", False)
    requires_email          = parsed.get("requires_email", False)
    requires_linkedin       = parsed.get("requires_linkedin", False)
    requires_confirmation  = parsed.get("requires_confirmation", False)
    task_complete          = parsed.get("task_complete", False)
    next_node              = parsed.get("next_node", "output_formatter")

    if not original_task:
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

    # Build the new message list (supervisor's response appended)
    new_messages = messages + [
        HumanMessage(content=f"{LABEL['supervisor']}\n{response_text}")
    ]

    # ── Context usage snapshot ─────────────────────────────────────────────────
    # Supervisor has no tools → include_tool_schemas=False
    try:
        session_id = state.get("slack_thread_ts", "")
        project_name = f"assistant\\{state.get('project_name', '')}"
        system_prompt = agent.get_system_prompt()

        snapshot_context_usage(
            session_id=session_id,
            project_name=project_name,
            messages=new_messages,
            system_prompt=system_prompt,
            model=agent.model_name,
            include_tool_schemas=False,
            task_complete=False,  # supervisor does not reset context; conversation persists
            workspace_path="",  # not used by supervisor
            agent_notes_enabled=False,  # not used when include_tool_schemas=False
        )
    except Exception as e:
        logger.warning(f"[supervisor] Context snapshot failed: {e}")

    # Launch UI if not already running (idempotent)
    try:
        launch_context_ui()
    except Exception as e:
        logger.warning(f"[supervisor] UI launch failed: {e}")

    return {
        "messages": new_messages,
        "original_task":           original_task,
        "current_subtask":         new_subtask,
        "current_subtask_id":      new_subtask_id,
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
