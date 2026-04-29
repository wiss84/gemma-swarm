"""
Gemma Swarm — Coding Subagent
==============================
A single unified subagent that mirrors the main CodingAgent's full toolset.
Used by the main agent to delegate focused subtasks with an isolated context window.

Design:
    - Full toolset (all layers including Layer 7 semantic tools) — identical to CodingAgent
    - Uses gemma-4-26b-a4b-it (fast MoE, same 256k context)
    - Lean system prompt: workspace + platform + workflow + output contract only
    - No user personalization, no Slack formatting rules, no subagent spawning

Why a subagent at all:
    Context window hygiene. The subagent runs in a completely fresh context,
    does its focused work, and returns only a concise summary. This keeps the
    main agent's context clean during long multi-step sessions.

Usage (called internally by CodingAgent.spawn_subagent):
    subagent = CodingSubagent(workspace_path="/path/to/project")
    result, _ = subagent.run(
        messages=[HumanMessage(content="<self-contained task description>")]
    )
    # result is a concise summary — not the full working history
"""

import logging
from coding_agent.subagents.base_subagent import BaseSubagent
from coding_agent.prompts.subagent_prompt import get_system_prompt as get_subagent_prompt
from tools.coding_tools import (
    read_files, write_files,
    edit_files, glob_search, grep_search, execute_shell,
    set_coding_workspace_root,
)
from tools.knowledge_tools import (
    get_installed_package_info,
    get_package_latest_version,
    fetch_package_docs,
)
from tools.web_search_tool import search_web, fetch_page, fetch_next_chunk
from tools.project_tools import (
    read_project_structure, read_requirements, read_git_log,
)
from tools.validation_tools_universal import (
    validate_files,
)
from tools.git_tools import (
    git_status, git_diff, git_commit, git_restore_file,
)
from tools.env_tools import (
    get_python_info, get_env_variables, install_package,
)
from tools.analyze_module_deps import analyze_module_dependencies
from tools.find_references import find_references
from tools.get_symbol_definition import get_symbol_definition
from tools.rename_symbol import rename_symbol

logger = logging.getLogger(__name__)


class CodingSubagent(BaseSubagent):
    """
    Single unified subagent for all coding subtasks.
    Full toolset, lean prompt, isolated context.
    """

    def __init__(self, workspace_path: str = ""):
        super().__init__(agent_name="coding_subagent", workspace_path=workspace_path)

        # Propagate workspace root to all tools
        set_coding_workspace_root(workspace_path)

        self.register_tools([
            # Layer 1 — workspace
            read_files, write_files,
            edit_files, glob_search, grep_search, execute_shell,
            # Layer 2 — knowledge / anti-hallucination
            get_installed_package_info, get_package_latest_version,
            fetch_package_docs, search_web, fetch_page, fetch_next_chunk,
            # Layer 3 — project understanding
            read_project_structure, read_requirements, read_git_log,
            # Layer 4 — validation
            validate_files,
            # Layer 5 — git (no git_restore_all — requires Slack context)
            git_status, git_diff, git_commit, git_restore_file,
            # Layer 6 — environment
            get_python_info, get_env_variables, install_package,
            # Layer 7 — semantic code intelligence
            find_references, get_symbol_definition, rename_symbol, analyze_module_dependencies,
        ])

        logger.info(
            f"[coding_subagent] Initialized with workspace='{workspace_path}', "
            f"{len(self.tools)} tools registered."
        )

    def get_system_prompt(self) -> str:
        return get_subagent_prompt(self.workspace_path)
