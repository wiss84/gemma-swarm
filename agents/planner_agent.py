"""
Gemma Swarm — Planner Agent
==============================
27b model. Runs once per complex user message.
Breaks the request into an ordered subtask list and stores it in state.

Does NOT execute any tasks — just plans.
"""

import re
import json
import logging
from datetime import datetime
from langchain_core.messages import HumanMessage
from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from agents_utils.config import LABEL

logger = logging.getLogger(__name__)

VALID_AGENTS = {"researcher", "deep_researcher", "email_composer", "linkedin_composer"}


class PlannerAgent(BaseAgent):

    def __init__(self):
        super().__init__("planner")

    def get_system_prompt(self) -> str:
        from system_prompts.planner_prompt import get_prompt
        return get_prompt()

    def plan(self, user_message: str, messages: list) -> dict | None:
        task_message = HumanMessage(content=f"{LABEL['human']}\n{user_message}")
        response_text, parsed = self.run(messages=messages + [task_message])

        if parsed and "subtasks" in parsed:
            return parsed

        # Try manual extraction
        match = re.search(r'\{[\s\S]+\}', response_text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        logger.error(f"[planner] Could not parse plan: {response_text[:200]}")
        return None


# ── LangGraph Node ─────────────────────────────────────────────────────────────

_planner_agent = None

def get_planner_agent() -> PlannerAgent:
    global _planner_agent
    if _planner_agent is None:
        _planner_agent = PlannerAgent()
    return _planner_agent


def planner_agent_node(state: AgentState) -> dict:
    """
    Builds a task plan and writes TASK STARTED to notes.
    Routes to supervisor which will execute subtasks one by one.
    """
    agent          = get_planner_agent()
    messages       = state.get("messages", [])
    workspace_path = state.get("workspace_path", "")
    original_task  = state.get("original_task", "")

    # Extract latest human message for planning
    latest_message = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content.startswith(LABEL["human"]):
                latest_message = content.replace(LABEL["human"], "").strip()
                break

    logger.info(f"[planner] Planning: {latest_message[:80]}")

    plan = agent.plan(user_message=latest_message, messages=messages)

    if not plan:
        # Fallback — treat as simple task, let supervisor handle it
        logger.warning("[planner] Planning failed — falling back to supervisor.")
        return {
            "is_complex_task":   False,
            "task_plan":         [],
                "next_node":         "supervisor",
        }

    subtasks = plan.get("subtasks", [])
    summary  = plan.get("summary", original_task[:100])

    # Validate and sanitize subtasks
    clean_subtasks = []
    for i, s in enumerate(subtasks):
        agent_name = s.get("agent", "researcher")
        if agent_name not in VALID_AGENTS:
            agent_name = "researcher"
        clean_subtasks.append({
            "id":          i + 1,
            "description": s.get("description", f"Subtask {i+1}"),
            "agent":       agent_name,
            "status":      "pending",
        })


    return {
        "task_plan":         clean_subtasks,
        "next_node":         "supervisor",
        "messages": messages + [
            HumanMessage(
                content=f"{LABEL['planner']}\nPlan ready: {len(clean_subtasks)} subtasks.\n"
                        + "\n".join(f"{s['id']}. [{s['agent']}] {s['description']}" for s in clean_subtasks)
            )
        ],
    }
