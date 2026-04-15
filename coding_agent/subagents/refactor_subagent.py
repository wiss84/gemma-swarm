"""
Gemma Swarm — Coding Agent: Refactor Subagent
==============================================
Specialized subagent for refactoring a specific file or module.

Allowed tools (Layer 1 workspace subset + Layer 4 linter only):
    read_file       — read a file's content
    write_file      — write or overwrite a file
    edit_file       — targeted string replacement with diff output
    glob_search     — find files matching a pattern
    grep_search     — search files for a regex pattern
    run_linter      — ruff / flake8 linter

NOT available to this subagent:
    execute_shell, git operations, knowledge tools, test runner, type checker.
    This subagent reads, edits, and lints — it does not run code or install packages.

Usage:
    subagent = RefactorSubagent(workspace_path="/path/to/project")
    result, _ = subagent.run(
        messages=[HumanMessage(content="Refactor utils.py to use dataclasses instead of dicts")]
    )
    # result is a concise summary of changes made and lint status
"""

import logging
from coding_agent.subagents.base_subagent import BaseSubagent
from coding_agent.prompts.refactor_subagent_prompt import get_system_prompt as get_refactor_prompt

from tools.coding_tools import (
    read_file,
    write_file,
    edit_file,
    glob_search,
    grep_search,
)
from tools.validation_tools import run_linter

logger = logging.getLogger(__name__)


class RefactorSubagent(BaseSubagent):
    """
    Refactor subagent — reads, edits, and lints files within a scoped context.
    Returns a concise summary of changes made and lint status.
    """

    def __init__(self, workspace_path: str = ""):
        super().__init__(agent_name="coding_refactor", workspace_path=workspace_path)
        self.register_tools([
            read_file,
            write_file,
            edit_file,
            glob_search,
            grep_search,
            run_linter,
        ])
        logger.info(f"[coding_refactor] Registered {len(self.tools)} tools.")

    def get_system_prompt(self) -> str:
        return get_refactor_prompt(self.workspace_path)
