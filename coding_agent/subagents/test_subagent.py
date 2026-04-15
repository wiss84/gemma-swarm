"""
Gemma Swarm — Coding Agent: Test Subagent
==========================================
Specialized subagent for writing and running tests.

Allowed tools (Layer 1 file tools + Layer 4 test/import tools + execute_shell):
    read_file       — read source files to understand what needs testing
    write_file      — write test files
    execute_shell   — run arbitrary commands (for custom test setups)
    run_tests       — pytest / unittest runner with output capture
    check_imports   — verify all imports resolve before running tests

NOT available to this subagent:
    edit_file, glob_search, grep_search, git tools, knowledge tools, linter, type checker.
    This subagent reads source files, writes test files, and runs them — nothing else.

Usage:
    subagent = TestSubagent(workspace_path="/path/to/project")
    result, _ = subagent.run(
        messages=[HumanMessage(content="Write and run tests for tools/coding_tools.py")]
    )
    # result is a concise summary of tests written and pass/fail results
"""

import logging
from coding_agent.subagents.base_subagent import BaseSubagent
from coding_agent.prompts.test_subagent_prompt import get_system_prompt as get_test_prompt

from tools.coding_tools import read_file, write_file, execute_shell
from tools.validation_tools import run_tests, check_imports

logger = logging.getLogger(__name__)


class TestSubagent(BaseSubagent):
    """
    Test subagent — reads source files, writes test files, and runs them.
    Returns a concise summary of tests written and pass/fail results.
    """

    def __init__(self, workspace_path: str = ""):
        super().__init__(agent_name="coding_test", workspace_path=workspace_path)
        self.register_tools([
            read_file,
            write_file,
            execute_shell,
            run_tests,
            check_imports,
        ])
        logger.info(f"[coding_test] Registered {len(self.tools)} tools.")

    def get_system_prompt(self) -> str:
        return get_test_prompt(self.workspace_path)
