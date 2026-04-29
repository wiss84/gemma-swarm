"""
Gemma Swarm — Coding Agent: Project TODO Tool
==============================================
A single tool with four operations for managing the project task log.

The tool OWNS project_TODO.md — it is the only writer. The file is
append-only: new content is always added at the bottom, existing content
is never modified or overwritten. This means the full task history
accumulates across all sessions and the agent can read it to understand
what was done in previous sessions.

Operations:
    start_task(task_name, steps)
        Called when the agent begins working on a task. Writes a new dated
        task block with all planned steps listed as [ ] (not started).
        Creates the file with a project header if it does not exist yet.
        Saves task_name and steps in module memory for use by other operations.

    update_step(step_index, status, note="")
        Called after completing, starting, or blocking a step.
        Rewrites the status marker of that step in the live task block:
            [x] = done
            [~] = in progress
            [!] = blocked
        step_index is 0-based (first step = 0).
        Optional note is appended to the step line for context.

    add_step(step_description)
        Called when the agent discovers a new unplanned step mid-task.
        Appends a new [ ] step to the live task block and to module memory.

    complete_task(result)
        Called after git_commit, when the main task is fully done.
        Appends the completion block with the final result and Status: Done.
        All step progress is already tracked live — no need to re-list steps.
        Returns a result string containing TASK_COMPLETE so the graph
        can detect task completion and reset the context window.

File format (append-only):

    # <project_name> - Task Log

    ---
    ## 2025-04-25 14:32 | Task: Create calculator module
    ### Status: In Progress
    ### Plan
    - [ ] Read project structure and requirements
    - [ ] Research calculator libraries if needed
    - [ ] Write calculator.py with 4 operations
    - [ ] Write tests/test_calculator.py
    - [ ] Validate and fix any errors
    - [ ] Commit

    (steps update live as agent progresses)

    - [x] Read project structure and requirements
    - [x] Research calculator libraries if needed
    - [x] Write calculator.py with 4 operations
    - [x] Write tests/test_calculator.py
    - [x] Validate and fix any errors — fixed 1 import error
    - [x] Commit

    ---
    ## 2025-04-25 14:45 | Task: Create calculator module
    ### Result: Calculator module complete — 8/8 tests passed, committed.
    ### Status: Done
    ---
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from langchain_core.tools import tool

from tools.coding_tools import _workspace_root

logger = logging.getLogger(__name__)

TODO_FILENAME = "project_TODO.md"

# Module-level memory for the current task.
_current_task_name: str = ""
_current_steps: list[str] = []          # raw description strings
_current_step_statuses: list[str] = []  # " ", "x", "~", "!"
_current_step_notes: list[str] = []     # optional note per step
_task_start_ts: str = ""


# -- Internal helpers ----------------------------------------------------------

def _todo_path() -> Path:
    return _workspace_root() / TODO_FILENAME


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _project_name() -> str:
    workspace = _workspace_root()
    raw = workspace.name
    return raw.replace("_", " ").replace("-", " ").title()


def _ensure_file_exists(todo_path: Path) -> None:
    if not todo_path.exists():
        header = (
            f"# {_project_name()} - Task Log\n\n"
            "_This file is the agent's persistent memory. "
            "Each task is recorded here so the agent can understand "
            "what was done in previous sessions._\n"
        )
        todo_path.write_text(header, encoding="utf-8")
        logger.info(f"[todo_tools] Created {todo_path}")


def _append(todo_path: Path, content: str) -> None:
    with open(todo_path, "a", encoding="utf-8") as f:
        f.write("\n" + content)


def _build_steps_block() -> str:
    """Renders the current in-memory steps as a markdown checklist."""
    lines = []
    for i, desc in enumerate(_current_steps):
        marker = _current_step_statuses[i] if i < len(_current_step_statuses) else " "
        note = _current_step_notes[i] if i < len(_current_step_notes) else ""
        line = f"- [{marker}] {desc}"
        if note:
            line += f" — {note}"
        lines.append(line)
    return "\n".join(lines)


def _rewrite_steps_in_file(todo_path: Path) -> None:
    """
    Appends the current step snapshot to the file.
    Since the file is append-only, each update_step/add_step call
    appends a fresh snapshot of the current step list so progress
    is always visible at the bottom of the file.
    """
    ts = _timestamp()
    block = (
        f"_[{ts}] Step update:_\n"
        f"{_build_steps_block()}\n"
    )
    _append(todo_path, block)


# -- Tool schema ---------------------------------------------------------------

class UpdateProjectTodoInput(BaseModel):
    operation: Literal["start_task", "update_step", "add_step", "complete_task"] = Field(
        description=(
            "Which operation to perform:\n"
            "  start_task    -- FIRST call when beginning a task. Pass task_name + full steps plan.\n"
            "  update_step   -- After finishing, starting, or blocking a step. Pass step_index + status.\n"
            "  add_step      -- When a new unplanned step is discovered mid-task. Pass step_description.\n"
            "  complete_task -- After git_commit when the task is fully done. Pass result only."
        )
    )
    task_name: str = Field(
        default="",
        description=(
            "Short descriptive name for the task. "
            "REQUIRED for start_task. Ignored for all other operations. "
            "Example: 'Add authentication module'."
        )
    )
    steps: list[str] = Field(
        default_factory=list,
        description=(
            "REQUIRED for start_task. The full planned step list as plain strings. "
            "Each string is one step. These will be written as [ ] checkboxes. "
            "Example: ['Read project structure', 'Research edge-tts API', "
            "'Write voice_engine.py', 'Write tests', 'Validate', 'Commit']. "
            "Ignored for all other operations."
        )
    )
    step_index: int = Field(
        default=-1,
        description=(
            "REQUIRED for update_step. 0-based index of the step to update. "
            "First step = 0. Ignored for all other operations."
        )
    )
    status: str = Field(
        default="",
        description=(
            "REQUIRED for update_step. One of: 'x' (done), '~' (in progress), '!' (blocked). "
            "Ignored for all other operations."
        )
    )
    note: str = Field(
        default="",
        description=(
            "Optional for update_step. Short context note appended to the step line. "
            "Example: 'fixed deprecated Communicate() call'. "
            "Ignored for all other operations."
        )
    )
    step_description: str = Field(
        default="",
        description=(
            "REQUIRED for add_step. Description of the new unplanned step to add. "
            "It will be added as [ ] (not started). "
            "Example: 'Fix missing __init__.py in package'. "
            "Ignored for all other operations."
        )
    )
    result: str = Field(
        default="",
        description=(
            "REQUIRED for complete_task. One-line summary of the final outcome. "
            "Example: 'Voice engine implemented — 6/6 tests passed, committed.' "
            "Ignored for all other operations."
        )
    )


# -- Tool ----------------------------------------------------------------------

@tool(args_schema=UpdateProjectTodoInput)
def update_project_todo(
    operation: str,
    task_name: str = "",
    steps: list[str] = None,
    step_index: int = -1,
    status: str = "",
    note: str = "",
    step_description: str = "",
    result: str = "",
) -> str:
    """
    Manage the project task log (project_TODO.md).

    WHEN TO CALL EACH OPERATION:
    - start_task:    First tool call when you begin executing a task.
                     Pass the full planned step list upfront as [ ] checkboxes.
                     Do NOT call for pure conversations or brainstorming.
    - update_step:   After you finish, start, or get blocked on any step.
                     Pass the step_index (0-based) and status: 'x', '~', or '!'.
    - add_step:      When you discover a new unplanned step mid-task.
                     Pass the step description — it will be added as [ ].
    - complete_task: After git_commit, when the task is fully done.
                     Pass only the result summary. Steps are already tracked.
                     Returns a string containing TASK_COMPLETE on success.
    """
    global _current_task_name, _current_steps, _current_step_statuses
    global _current_step_notes, _task_start_ts

    if steps is None:
        steps = []

    try:
        todo_path = _todo_path()
        _ensure_file_exists(todo_path)
        ts = _timestamp()

        # -- start_task --------------------------------------------------------
        if operation == "start_task":
            if not task_name or not task_name.strip():
                return (
                    "[update_project_todo error: task_name is required for start_task. "
                    "Example: update_project_todo(operation='start_task', task_name='Add voice engine', "
                    "steps=['Read structure', 'Research edge-tts', 'Write code', 'Validate', 'Commit'])]"
                )
            if not steps:
                return (
                    "[update_project_todo error: steps is required for start_task. "
                    "Provide the full planned step list as a list of strings. "
                    "Example: steps=['Read project structure', 'Research edge-tts API', "
                    "'Write voice_engine.py', 'Write tests', 'Validate', 'Commit']]"
                )

            _current_task_name = task_name.strip()
            _current_steps = [s.strip() for s in steps if s.strip()]
            _current_step_statuses = [" "] * len(_current_steps)
            _current_step_notes = [""] * len(_current_steps)
            _task_start_ts = ts

            steps_block = _build_steps_block()
            block = (
                "---\n"
                f"## {ts} | Task: {_current_task_name}\n"
                "### Status: In Progress\n"
                "### Plan\n"
                f"{steps_block}\n"
            )
            _append(todo_path, block)
            logger.info(f"[todo_tools] start_task: '{_current_task_name}' — {len(_current_steps)} steps")
            return (
                f"update_project_todo: Task started and logged\n"
                f"Task: {_current_task_name}\n"
                f"Steps: {len(_current_steps)}\n"
                f"Time: {ts}\n"
                f"File: {todo_path}"
            )

        # -- update_step -------------------------------------------------------
        elif operation == "update_step":
            if not _current_task_name:
                return (
                    "[update_project_todo error: no active task. "
                    "Call start_task before update_step.]"
                )
            if step_index < 0 or step_index >= len(_current_steps):
                return (
                    f"[update_project_todo error: step_index {step_index} is out of range. "
                    f"Valid range: 0 to {len(_current_steps) - 1}.]"
                )
            if status not in ("x", "~", "!"):
                return (
                    "[update_project_todo error: status must be 'x' (done), '~' (in progress), "
                    "or '!' (blocked).]"
                )

            _current_step_statuses[step_index] = status
            if note:
                _current_step_notes[step_index] = note.strip()

            _rewrite_steps_in_file(todo_path)

            status_label = {"x": "Done", "~": "In Progress", "!": "Blocked"}.get(status, status)
            logger.info(f"[todo_tools] update_step: step {step_index} → [{status}] {status_label}")
            return (
                f"update_project_todo: Step {step_index} updated to [{status}] {status_label}\n"
                f"Step: {_current_steps[step_index]}\n"
                f"Note: {note or '(none)'}"
            )

        # -- add_step ----------------------------------------------------------
        elif operation == "add_step":
            if not _current_task_name:
                return (
                    "[update_project_todo error: no active task. "
                    "Call start_task before add_step.]"
                )
            if not step_description or not step_description.strip():
                return (
                    "[update_project_todo error: step_description is required for add_step. "
                    "Example: update_project_todo(operation='add_step', "
                    "step_description='Fix missing __init__.py')]"
                )

            new_step = step_description.strip()
            _current_steps.append(new_step)
            _current_step_statuses.append(" ")
            _current_step_notes.append("")
            new_index = len(_current_steps) - 1

            _rewrite_steps_in_file(todo_path)

            logger.info(f"[todo_tools] add_step: index {new_index} — '{new_step}'")
            return (
                f"update_project_todo: New step added at index {new_index}\n"
                f"Step: {new_step}\n"
                f"Total steps: {len(_current_steps)}"
            )

        # -- complete_task -----------------------------------------------------
        elif operation == "complete_task":
            if not _current_task_name:
                return (
                    "[update_project_todo error: no active task. "
                    "Call start_task before complete_task.]"
                )
            if not result or not result.strip():
                return (
                    "[update_project_todo error: result is required for complete_task. "
                    "Provide a one-line summary of the outcome. "
                    "Example: result='Voice engine implemented — 6/6 tests passed, committed.']"
                )

            resolved_name = _current_task_name

            block = (
                "---\n"
                f"## {ts} | Task: {resolved_name}\n"
                f"### Result: {result.strip()}\n"
                "### Status: Done\n"
                "---\n"
            )
            _append(todo_path, block)

            # Clear module memory
            _current_task_name = ""
            _current_steps = []
            _current_step_statuses = []
            _current_step_notes = []
            _task_start_ts = ""

            logger.info(f"[todo_tools] complete_task: '{resolved_name}'")
            return (
                f"update_project_todo: Task completed and logged\n"
                f"Task: {resolved_name}\n"
                f"Time: {ts}\n"
                f"File: {todo_path}\n"
                f"TASK_COMPLETE"
            )

        else:
            return (
                f"[update_project_todo error: unknown operation '{operation}'. "
                f"Use 'start_task', 'update_step', 'add_step', or 'complete_task']"
            )

    except Exception as e:
        logger.error(f"[todo_tools] Error in {operation}: {e}", exc_info=True)
        return f"[update_project_todo error: {e}]"
