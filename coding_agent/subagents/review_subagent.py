"""
Gemma Swarm — Coding Agent: Review Subagent
============================================
Specialized subagent for reviewing code changes and diffs.

Allowed tools (read-only tools + analysis tools):
    read_file           — read any file to review its content
    grep_search         — search for patterns across files
    git_diff            — show what changed as a unified diff
    run_linter          — ruff / flake8 linter
    run_type_checker    — mypy type checker (skips gracefully if not installed)

NOT available to this subagent:
    write_file, edit_file, execute_shell, test runner, git commit/branch tools.
    This subagent ONLY reads and reports — it does not modify anything.

Usage:
    subagent = ReviewSubagent(workspace_path="/path/to/project")
    result, _ = subagent.run(
        messages=[HumanMessage(content="Review the changes on branch feat/add-fibonacci")]
    )
    # result is a structured review report: issues found, severity, suggestions
"""

import logging
from coding_agent.subagents.base_subagent import BaseSubagent
from coding_agent.prompts.review_subagent_prompt import get_system_prompt as get_review_prompt

from tools.coding_tools import read_file, grep_search
from tools.git_tools import git_diff
from tools.validation_tools import run_linter, run_type_checker

logger = logging.getLogger(__name__)


class ReviewSubagent(BaseSubagent):
    """
    Review subagent — reads diffs and files, runs static analysis, reports issues.
    Returns a structured review report with issues, severities, and suggestions.
    """

    def __init__(self, workspace_path: str = ""):
        super().__init__(agent_name="coding_review", workspace_path=workspace_path)
        self.register_tools([
            read_file,
            grep_search,
            git_diff,
            run_linter,
            run_type_checker,
        ])
        logger.info(f"[coding_review] Registered {len(self.tools)} tools.")

    def get_system_prompt(self) -> str:
        return get_review_prompt(self.workspace_path)
