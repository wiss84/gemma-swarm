"""
Gemma Swarm — Coding Agent: Base Subagent
==========================================
Shared base class for all coding subagents. Extends BaseAgent with:
    - workspace_path attribute (required — every subagent is scoped to a directory)
    - run() override that defaults to the subagent's own CODING_MAX_TOOL_ITERATIONS
    - Abstract get_system_prompt() — each subagent defines its own tight scope

Design rules:
    - Subagents have ISOLATED context windows — they receive only the task brief,
      not the main agent's conversation history.
    - Subagents return ONLY a concise summary of what they did and found.
      They must NOT return their full working history or raw tool output dumps.
    - Subagents do NOT spawn other subagents. If a subagent needs to delegate,
      that is a sign the task should be handled by the main agent instead.
    - Each subagent registers ONLY its allowed tool subset (defined in the
      concrete subclass). No tool creep.
"""

import logging
from abc import abstractmethod
from langchain_core.messages import HumanMessage
from agents.base_agent import BaseAgent
from agents_utils.config import CODING_MAX_TOOL_ITERATIONS

logger = logging.getLogger(__name__)


class BaseSubagent(BaseAgent):
    """
    Shared base for all coding subagents.

    Subclasses must:
        1. Call super().__init__(agent_name, workspace_path) with their agent name.
        2. Call self.register_tools([...]) with their specific tool subset.
        3. Implement get_system_prompt() with a tightly scoped prompt.

    Args:
        agent_name:     The config key, e.g. "coding_research", "coding_refactor".
        workspace_path: The project directory this subagent is scoped to.
    """

    def __init__(self, agent_name: str, workspace_path: str = ""):
        super().__init__(agent_name=agent_name)
        self.workspace_path = workspace_path
        logger.info(
            f"[{agent_name}] Subagent initialized | workspace='{workspace_path}'"
        )

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return a tightly scoped system prompt for this subagent's role."""
        pass

    def run(self, messages, extra_context="", max_tool_iterations=None, state=None):
        """
        Run the subagent. Defaults to this subagent's own CODING_MAX_TOOL_ITERATIONS
        entry rather than the global MAX_TOOL_ITERATIONS.

        The state dict is pre-populated with workspace_path so tools have
        access to it without needing it passed explicitly every time.
        """
        if max_tool_iterations is None:
            max_tool_iterations = CODING_MAX_TOOL_ITERATIONS.get(self.agent_name, 10)

        if state is None:
            state = {}
        if self.workspace_path and "workspace_path" not in state:
            state["workspace_path"] = self.workspace_path

        return super().run(
            messages=messages,
            extra_context=extra_context,
            max_tool_iterations=max_tool_iterations,
            state=state,
        )
