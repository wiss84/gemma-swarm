"""
Gemma Swarm — Coding Agent: Main CodingAgent
=============================================
The orchestrating agent for all coding tasks. Extends BaseAgent with:
    - All 6 tool layers + Layer 7 semantic tools registered
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
    The main agent can delegate subtasks to a CodingSubagent via spawn_subagent.
    The subagent has the full toolset, an isolated context window, and returns
    only a concise summary of its work. No subagent_type needed — one subagent
    handles all task types.
"""

import logging
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.base_agent import BaseAgent
from agents_utils.config import CODING_MAX_TOOL_ITERATIONS, MODELS
from coding_agent.prompts.main_agent_prompt import get_system_prompt as get_main_prompt

# ── Layer 1: Workspace tools ───────────────────────────────────────────────────
from tools.coding_tools import (
    read_files,
    write_files,
    edit_files,
    glob_search,
    grep_search,
    execute_shell,
    set_coding_workspace_root,
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
    read_requirements,
    read_git_log,
)

# ── Layer 4: Validation tools ──────────────────────────────────────────────────
from tools.validation_tools_universal import (
    validate_files,
)

# ── Layer 5: Git tools ─────────────────────────────────────────────────────────────
from tools.git_tools import (
    git_status,
    git_diff,
    git_commit,
    git_restore_file,
)

# ── Layer 6: Environment tools ─────────────────────────────────────────────────
from tools.env_tools import (
    get_python_info,
    get_env_variables,
    install_package,
)

# ── Layer 7: Semantic code intelligence tools ──────────────────────────────────
from tools.analyze_module_deps import analyze_module_dependencies
from tools.find_references import find_references
from tools.get_symbol_definition import get_symbol_definition
from tools.rename_symbol import rename_symbol

# ── Agent learning notes tools ──────────────────────────────────────────────────
from tools.agent_notes import read_agent_notes, write_agent_note

# ── Todo tool ────────────────────────────────────────────────────────────────
from tools.todo_tools import update_project_todo

logger = logging.getLogger(__name__)

# ── spawn_subagent tool schema ─────────────────────────────────────────────────

class SpawnSubagentInput(BaseModel):
    task:        str = Field(
        description=(
            "A concise, self-contained task description for the subagent. "
            "Include ALL necessary context — the subagent has no memory of the main conversation. "
            "Example: 'Research the httpx 0.27 async API and write a summary to research/httpx_notes.md'"
        )
    )
    working_dir: str = Field(
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

    def __init__(self, workspace_path: str = "", model_override: str = "", agent_notes_enabled: bool = True, status_callback=None):
        effective_model = model_override or MODELS.get("coding_agent")
        super().__init__(agent_name="coding_agent", model_name=effective_model, status_callback=status_callback)
        self.workspace_path = workspace_path
        self.agent_notes_enabled = agent_notes_enabled

        # Propagate workspace root to all tools so they use the correct directory
        set_coding_workspace_root(workspace_path)

        # Pick up any Slack rate-limit callback registered by run_coding_session_slack()
        # so the agent posts countdown messages when RPM limits are hit.
        try:
            from slack_utils.handlers_coding import get_coding_rate_callback
            cb = get_coding_rate_callback()
            if cb:
                self.rate_limiter.on_wait = cb
        except Exception:
            pass  # Not running in Slack context — no callback needed

        # Build the spawn_subagent tool bound to this instance
        spawn_tool = self._build_spawn_tool()

        # Base tool list — always included
        tools = [
            # Layer 1
            read_files, write_files, edit_files,
            glob_search, grep_search, execute_shell,
            # Layer 2
            get_installed_package_info, get_package_latest_version,
            fetch_package_docs, search_web, fetch_page, fetch_next_chunk,
            # Layer 3
            read_project_structure,
            read_requirements, read_git_log,
            # Layer 4
            validate_files,
            # Layer 5: Git tools
            git_status, git_diff, git_commit, git_restore_file,
            # Layer 6
            get_python_info, get_env_variables, install_package,
            # Layer 7 — semantic code intelligence
            find_references, get_symbol_definition, rename_symbol, analyze_module_dependencies,
            # Subagent spawner
            spawn_tool,
            # Todo tool
            update_project_todo,
        ]

        # Conditionally add agent notes tools
        if agent_notes_enabled:
            tools.append(read_agent_notes)
            tools.append(write_agent_note)

        self.register_tools(tools)

        logger.info(
            f"[coding_agent] Initialized with workspace='{workspace_path}', "
            f"{len(self.tools)} tools registered "
            f"(notes {'enabled' if agent_notes_enabled else 'disabled'})."
        )

    # ── System prompt ──────────────────────────────────────────────────────────

    def get_system_prompt(self) -> str:
        return get_main_prompt(self.workspace_path, self.agent_notes_enabled)

    # ── spawn_subagent ─────────────────────────────────────────────────────────

    def _build_spawn_tool(self):
        """
        Build the spawn_subagent tool bound to this CodingAgent instance.
        Returned as a LangChain @tool so it can be registered and called
        naturally in the agent's tool loop.
        """
        agent_self = self  # capture for closure

        @tool(args_schema=SpawnSubagentInput)
        def spawn_subagent(task: str, working_dir: str = "") -> str:
            """
            Delegate a focused subtask to a subagent with an isolated context window.
            The subagent has the full toolset and returns only a concise summary of its work.
            Use this when a subtask is independent and its output would pollute the main context.
            Always include ALL necessary context in the task description — the subagent
            has no memory of this conversation.
            Returns the subagent's summary, or an error string starting with '['.
            """
            return agent_self.spawn_subagent(
                task=task,
                working_dir=working_dir,
            )

        return spawn_subagent

    def spawn_subagent(self, task: str, working_dir: str = "") -> str:
        """
        Create a fresh CodingSubagent with isolated context, run the task,
        and return only the final text result (not the full message history).

        Args:
            task:        Self-contained task description. Must include all needed context.
            working_dir: Working directory for the subagent. Defaults to self.workspace_path.

        Returns:
            The subagent's final response string, or an error string starting with '['.
        """
        from coding_agent.subagents.coding_subagent import CodingSubagent

        effective_dir = working_dir or self.workspace_path

        logger.info(
            f"[coding_agent] Spawning subagent | "
            f"working_dir='{effective_dir}' | task='{task[:80]}...'"
        )

        try:
            subagent = CodingSubagent(workspace_path=effective_dir)
            max_iters = CODING_MAX_TOOL_ITERATIONS.get("coding_subagent", 100)
            result, parsed = subagent.run(
                messages=[HumanMessage(content=task)],
                max_tool_iterations=max_iters,
                state={"workspace_path": effective_dir},
            )
            
            # If the subagent hit max iterations, report it clearly to the main agent
            if parsed and parsed.get("error") == "max_iterations_reached":
                iters = parsed.get("iterations", max_iters)
                logger.warning(f"[coding_agent] Subagent hit max iterations ({iters})")
                return f"[spawn_subagent error: Subagent reached max iterations ({iters}) without completing the task. Result so far: {result}]"

            logger.info(
                f"[coding_agent] Subagent completed | result length={len(result)}"
            )
            return result

        except Exception as e:
            logger.error(f"[coding_agent] Subagent raised: {e}", exc_info=True)
            return f"[spawn_subagent error: Subagent failed: {e}]"

    # ── Convenience run override ───────────────────────────────────────────────

    def run(self, messages, extra_context="", max_tool_iterations=None, state=None, cancel_event=None):
        """
        Run the coding agent. Defaults to CODING_MAX_TOOL_ITERATIONS["coding_agent"].
        The workspace is its own independent git repo — the agent always works on
        main and commits directly, so no branch save/restore is needed.
        """
        if max_tool_iterations is None:
            max_tool_iterations = CODING_MAX_TOOL_ITERATIONS.get("coding_agent", 30)

        if state is None:
            state = {}
        if self.workspace_path and "workspace_path" not in state:
            state["workspace_path"] = self.workspace_path

        return super().run(
            messages=messages,
            extra_context=extra_context,
            max_tool_iterations=max_tool_iterations,
            state=state,
            cancel_event=cancel_event,
        )
