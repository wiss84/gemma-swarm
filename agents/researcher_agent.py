"""
Gemma Swarm — Researcher Agent
================================
Quick lookups only: search_web tool.
Use for: news, facts, prices, recent events.
For documentation or code examples use deep_researcher.

Model: gemma-3-12b-it (128k context)
"""

import logging
from pathlib import Path
from datetime import datetime
from langchain_core.messages import HumanMessage
from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from agents_utils.config import LABEL
from tools.web_search_tool import search_web

logger = logging.getLogger(__name__)


class ResearcherAgent(BaseAgent):

    def __init__(self):
        super().__init__("researcher")
        self.register_tools([search_web])

    def get_system_prompt(self) -> str:
        from system_prompts.researcher_prompt import get_prompt
        return get_prompt()

    def research(self, task: str, messages: list, state: dict = None) -> str:
        task_message = HumanMessage(content=f"{LABEL['supervisor']}\n{task}")
        response_text, _ = self.run(messages=messages + [task_message], state=state)
        return response_text

    def save_research(self, workspace_path: str, query: str, findings: str):
        research_dir = Path(workspace_path) / "research"
        research_dir.mkdir(exist_ok=True)
        timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_query = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in query[:40]
        ).lower()
        filepath = research_dir / f"{timestamp}_{safe_query}.md"
        try:
            filepath.write_text(f"# Research: {query}\n\n{findings}\n", encoding="utf-8")
            logger.info(f"[researcher] Saved to {filepath}")
        except OSError as e:
            logger.error(f"[researcher] Could not save: {e}")


# ── LangGraph Node ─────────────────────────────────────────────────────────────

_researcher_agent = None

def get_researcher_agent() -> ResearcherAgent:
    global _researcher_agent
    if _researcher_agent is None:
        _researcher_agent = ResearcherAgent()
    return _researcher_agent


def researcher_agent_node(state: AgentState) -> dict:
    agent              = get_researcher_agent()
    current_subtask    = state.get("current_subtask", "")
    messages           = state.get("messages", [])
    workspace_path     = state.get("workspace_path", "")
    subtask_results    = state.get("subtask_results", {})
    researcher_history = list(state.get("researcher_history", []))

    logger.info(f"[researcher] Starting: {current_subtask[:80]}")

    findings = agent.research(task=current_subtask, messages=messages, state=state)

    if workspace_path:
        agent.save_research(workspace_path, current_subtask, findings)

    logger.info(f"[researcher] Complete ({len(findings)} chars).")

    # Update researcher own history
    task_msg   = HumanMessage(content=f"{LABEL['supervisor']}\n{current_subtask}")
    result_msg = HumanMessage(content=f"{LABEL['researcher']}\n{findings}")
    researcher_history.extend([task_msg, result_msg])

    return {
        "subtask_results":    {**subtask_results, "research": findings},
        "active_agent":       "researcher",
        "next_node":          "supervisor",
        "researcher_history": researcher_history,
        "messages": messages + [result_msg],
    }
