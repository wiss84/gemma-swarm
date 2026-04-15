"""
Gemma Swarm — Coding Agent: Main CodingAgent
=============================================
The orchestrating agent for all coding tasks. Extends BaseAgent with:
    - All 6 tool layers registered
    - Higher MAX_TOOL_ITERATIONS (30) for complex multi-step tasks
    - workspace_path attribute set at session start
    - spawn_subagent() method exposed as a LangChain tool

Usage:
    agent = CodingAgent(workspace_path="/path/to/project")
    result, parsed = agent.run(
        messages=[HumanMessage(content="Add a fibonacci function to utils.py")],
        max_tool_iterations=30,
    )

Subagent spawning:
    The main agent can delegate subtasks to isolated subagents via the
    spawn_subagent tool. The subagent runs in a fresh context window and
    returns only a concise summary — not its full working history.
    This keeps the main agent's context clean during long sessions.
"""

import logging
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.base_agent import BaseAgent
from agents_utils.config import CODING_MAX_TOOL_ITERATIONS
from coding_agent.prompts.main_agent_prompt import get_system_prompt as get_main_prompt

# ── Layer 1: Workspace tools ───────────────────────────────────────────────────
from tools.coding_tools import (
    read_file,
    write_file,
    edit_file,
    list_dir,
    glob_search,
    grep_search,
    execute_shell,
)

# ── Layer 2: Knowledge / anti-hallucination tools ──────────────────────────────
from tools.knowledge_tools import (
    get_installed_package_info,
    get_package_latest_version,
    fetch_package_docs,
)
from tools.web_search_tool import search_web, fetch_page, fetch_next_chunk

# ── Layer 3: Project understanding tools ───────────────────────────────────────
from tools.project_tools import (
    read_project_structure,
    search_codebase,
    read_requirements,
    read_git_log,
)

# ── Layer 4: Validation tools ──────────────────────────────────────────────────
from tools.validation_tools import (
    run_tests,
    run_linter,
    check_imports,
    run_type_checker,
)

# ── Layer 5: Git tools ─────────────────────────────────────────────────────────
from tools.git_tools import (
    git_status,
    git_diff,
    git_commit,
    git_create_branch,
    git_restore_file,
    git_restore_all,
)

# ── Layer 6: Environment tools ─────────────────────────────────────────────────
from tools.env_tools import (
    get_python_info,
    get_env_variables,
    install_package,
)

logger = logging.getLogger(__name__)

# ── Subagent type → class (imported lazily to avoid circular imports) ──────────
SUBAGENT_MAP = {
    "research": "coding_agent.subagents.research_subagent.ResearchSubagent",
    "refactor": "coding_agent.subagents.refactor_subagent.RefactorSubagent",
    "test":     "coding_agent.subagents.test_subagent.TestSubagent",
    "review":   "coding_agent.subagents.review_subagent.ReviewSubagent",
}


def _import_subagent_class(dotted_path: str):
    """Dynamically import a subagent class by dotted module path."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


# ── spawn_subagent tool schema ─────────────────────────────────────────────────

class SpawnSubagentInput(BaseModel):
    task:         str = Field(
        description=(
            "A concise, self-contained task description for the subagent. "
            "Include all necessary context — the subagent has no memory of the main conversation. "
            "Example: 'Check PyPI for the latest version of httpx and summarize what changed in 0.27.x'"
        )
    )
    subagent_type: str = Field(
        description=(
            "Type of subagent to spawn. One of: 'research', 'refactor', 'test', 'review'.\n"
            "  research — web search, PyPI lookups, doc fetching\n"
            "  refactor — read/edit files, run linter\n"
            "  test     — write/run test files\n"
            "  review   — read files, git diff, linter, type checker"
        )
    )
    working_dir:  str = Field(
        default="",
        description="Working directory for the subagent. Defaults to the main agent's workspace_path."
    )


class CodingAgent(BaseAgent):
    """
    Main coding agent. Orchestrates all coding tasks using the full tool stack.

    Attributes:
        workspace_path (str): The project directory this session is scoped to.
            Set at instantiation — the agent always works within this directory.
    """

    def __init__(self, workspace_path: str = ""):
        super().__init__(agent_name="coding_agent")
        self.workspace_path = workspace_path

        # Build the spawn_subagent tool bound to this instance
        spawn_tool = self._build_spawn_tool()

        # Register all 6 layers + spawn_subagent
        self.register_tools([
            # Layer 1
            read_file, write_file, edit_file,
            list_dir, glob_search, grep_search, execute_shell,
            # Layer 2
            get_installed_package_info, get_package_latest_version,
            fetch_package_docs, search_web, fetch_page, fetch_next_chunk,
            # Layer 3
            read_project_structure, search_codebase,
            read_requirements, read_git_log,
            # Layer 4
            run_tests, run_linter, check_imports, run_type_checker,
            # Layer 5
            git_status, git_diff, git_commit,
            git_create_branch, git_restore_file, git_restore_all,
            # Layer 6
            get_python_info, get_env_variables, install_package,
            # Subagent spawner
            spawn_tool,
        ])

        logger.info(
            f"[coding_agent] Initialized with workspace='{workspace_path}', "
            f"{len(self.tools)} tools registered."
        )

    # ── System prompt ──────────────────────────────────────────────────────────

    def get_system_prompt(self) -> str:
        return get_main_prompt(self.workspace_path)

    # ── spawn_subagent ─────────────────────────────────────────────────────────

    def _build_spawn_tool(self):
        """
        Build the spawn_subagent tool bound to this CodingAgent instance.
        Returned as a LangChain @tool so it can be registered and called
        naturally in the agent's tool loop.
        """
        agent_self = self  # capture for closure

        @tool(args_schema=SpawnSubagentInput)
        def spawn_subagent(task: str, subagent_type: str, working_dir: str = "") -> str:
            """
            Delegate a focused subtask to a specialized subagent with an isolated context window.
            The subagent runs independently and returns only a concise summary of its findings.
            Use this when a subtask is independent, output-heavy, or needs a specialized tool subset.
            Subagent types: 'research', 'refactor', 'test', 'review'.
            Returns the subagent's final text response, or an error string starting with '['.
            """
            return agent_self.spawn_subagent(
                task=task,
                subagent_type=subagent_type,
                working_dir=working_dir,
            )

        return spawn_subagent

    def spawn_subagent(self, task: str, subagent_type: str, working_dir: str = "") -> str:
        """
        Create a fresh subagent with isolated context, run the task,
        and return only the final text result (not the full message history).

        Args:
            task:          Self-contained task description. Must include all needed context.
            subagent_type: One of "research", "refactor", "test", "review".
            working_dir:   Working directory for the subagent. Defaults to self.workspace_path.

        Returns:
            The subagent's final response string, or an error string starting with '['.
        """
        dotted_path = SUBAGENT_MAP.get(subagent_type)
        if not dotted_path:
            return (
                f"[spawn_subagent error: Unknown subagent type '{subagent_type}'. "
                f"Valid types: {', '.join(SUBAGENT_MAP.keys())}]"
            )

        effective_dir = working_dir or self.workspace_path

        logger.info(
            f"[coding_agent] Spawning '{subagent_type}' subagent | "
            f"working_dir='{effective_dir}' | task='{task[:80]}...'"
        )

        try:
            SubagentClass = _import_subagent_class(dotted_path)
        except (ImportError, AttributeError) as e:
            return (
                f"[spawn_subagent error: Could not import {dotted_path}: {e}. "
                f"Subagents will be available after Step 2.2 is complete.]"
            )

        try:
            subagent = SubagentClass(workspace_path=effective_dir)
            max_iters = CODING_MAX_TOOL_ITERATIONS.get(f"coding_{subagent_type}", 10)
            result, _ = subagent.run(
                messages=[HumanMessage(content=task)],
                max_tool_iterations=max_iters,
                state={"workspace_path": effective_dir},
            )
            logger.info(
                f"[coding_agent] '{subagent_type}' subagent completed | "
                f"result length={len(result)}"
            )
            return result

        except Exception as e:
            logger.error(f"[coding_agent] Subagent '{subagent_type}' raised: {e}", exc_info=True)
            return f"[spawn_subagent error: Subagent '{subagent_type}' failed: {e}]"

    # ── Convenience run override ───────────────────────────────────────────────

    def run(self, messages, extra_context="", max_tool_iterations=None, state=None):
        """
        Run the coding agent. Defaults to CODING_MAX_TOOL_ITERATIONS["coding_agent"]
        instead of the global MAX_TOOL_ITERATIONS so coding tasks get enough steps.
        """
        if max_tool_iterations is None:
            max_tool_iterations = CODING_MAX_TOOL_ITERATIONS.get("coding_agent", 30)

        # Inject workspace_path into state so tool calls have access to it
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
