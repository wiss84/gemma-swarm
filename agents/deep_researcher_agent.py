"""
Gemma Swarm — Deep Researcher Agent
======================================
Full research: search_web + fetch_page + fetch_next_chunk.
Use for: documentation, code examples, technical articles, URLs.

Model: gemma-3-12b-it (128k context)
"""

import logging
from pathlib import Path
from datetime import datetime
from langchain_core.messages import HumanMessage
from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from agents_utils.config import LABEL
from tools.web_search_tool import search_web, fetch_page, fetch_next_chunk

logger = logging.getLogger(__name__)


class DeepResearcherAgent(BaseAgent):

    def __init__(self):
        super().__init__("deep_researcher")
        self.register_tools([search_web, fetch_page, fetch_next_chunk])

    def get_system_prompt(self) -> str:
        from system_prompts.deep_researcher_prompt import get_prompt
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
        filepath = research_dir / f"{timestamp}_deep_{safe_query}.md"
        try:
            filepath.write_text(f"# Deep Research: {query}\n\n{findings}\n", encoding="utf-8")
            logger.info(f"[deep_researcher] Saved to {filepath}")
        except OSError as e:
            logger.error(f"[deep_researcher] Could not save: {e}")


# ── LangGraph Node ─────────────────────────────────────────────────────────────

_deep_researcher_agent = None

def get_deep_researcher_agent() -> DeepResearcherAgent:
    global _deep_researcher_agent
    if _deep_researcher_agent is None:
        _deep_researcher_agent = DeepResearcherAgent()
    return _deep_researcher_agent


def deep_researcher_agent_node(state: AgentState) -> dict:
    agent                   = get_deep_researcher_agent()
    current_subtask         = state.get("current_subtask", "")
    messages                = state.get("messages", [])
    workspace_path          = state.get("workspace_path", "")
    subtask_results         = state.get("subtask_results", {})
    deep_researcher_history = list(state.get("deep_researcher_history", []))

    logger.info(f"[deep_researcher] Starting: {current_subtask[:80]}")

    findings = agent.research(task=current_subtask, messages=messages, state=state)

    if workspace_path:
        agent.save_research(workspace_path, current_subtask, findings)

    logger.info(f"[deep_researcher] Complete ({len(findings)} chars).")

    # Update deep researcher own history
    task_msg   = HumanMessage(content=f"{LABEL['supervisor']}\n{current_subtask}")
    result_msg = HumanMessage(content=f"{LABEL['deep_researcher']}\n{findings}")
    deep_researcher_history.extend([task_msg, result_msg])

    return {
        "subtask_results":         {**subtask_results, "research": findings},
        "active_agent":            "deep_researcher",
        "next_node":               "supervisor",
        "deep_researcher_history": deep_researcher_history,
        "messages": messages + [result_msg],
    }
