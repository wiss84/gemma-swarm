"""
Gemma Swarm — Coding Agent: Research Subagent
==============================================
Specialized subagent for researching package versions, APIs, and documentation.

Allowed tools (Layer 2 knowledge tools + web tools only):
    search_web                  — DuckDuckGo search
    fetch_page                  — fetch any URL as clean text
    fetch_next_chunk            — read the next chunk of a large page
    get_installed_package_info  — pip show <package>
    get_package_latest_version  — PyPI JSON API
    fetch_package_docs          — readthedocs / PyPI docs page

NOT available to this subagent:
    File writing, shell execution, git operations, validation tools.
    This subagent only reads and searches — it does not modify anything.

Usage:
    subagent = ResearchSubagent(workspace_path="/path/to/project")
    result, _ = subagent.run(
        messages=[HumanMessage(content="What is the current API for httpx async client?")]
    )
    # result is a concise summary of findings — not raw tool output
"""

import logging
from coding_agent.subagents.base_subagent import BaseSubagent
from coding_agent.prompts.research_subagent_prompt import get_system_prompt as get_research_prompt

from tools.knowledge_tools import (
    get_installed_package_info,
    get_package_latest_version,
    fetch_package_docs,
)
from tools.web_search_tool import search_web, fetch_page, fetch_next_chunk

logger = logging.getLogger(__name__)


class ResearchSubagent(BaseSubagent):
    """
    Research subagent — looks up current package versions, APIs, and documentation.
    Returns a concise, actionable summary for the main agent to use when writing code.
    """

    def __init__(self, workspace_path: str = ""):
        super().__init__(agent_name="coding_research", workspace_path=workspace_path)
        self.register_tools([
            search_web,
            fetch_page,
            fetch_next_chunk,
            get_installed_package_info,
            get_package_latest_version,
            fetch_package_docs,
        ])
        logger.info(f"[coding_research] Registered {len(self.tools)} tools.")

    def get_system_prompt(self) -> str:
        return get_research_prompt(self.workspace_path)
