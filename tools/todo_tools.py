"""
Gemma Swarm — Coding Agent: Project TODO Tool
==============================================
A single tool with four operations for managing the project task log.

The tool OWNS project_TODO.md — it is the only writer. The ### Plan block
written by start_task is edited in-place as steps progress — checkboxes are
updated directly in the file. The full task history accumulates across all
sessions and the agent can read it to understand what was done previously.

Operations:
    start_task(task_name, steps)
        Called when the agent begins working on a task. Writes a new dated
        task block with all planned steps listed as [ ] (not started).
        Creates the file with a project header if it does not exist yet.
        Saves task_name and steps in module memory for use by other operations.

    update_step(step_index, status, note="")
        Called after completing, starting, or blocking one or more steps.
        Edits the checkbox(es) directly in the existing ### Plan block in the
        file — no new block is appended. Supports bulk updates:
            step_index: single int  -> update one step
            step_index: list[int]   -> update multiple steps in one call
        Markers:
            [x] = done
            [~] = in progress
            [!] = blocked
        step_index is 0-based (first step = 0).
        Optional note is appended to the step line (single step only).

    add_step(step_description)
        Called when the agent discovers one or more unplanned steps mid-task.
        Appends new [ ] line(s) to the existing ### Plan block.
        step_description accepts a single string or a list of strings:
            add_step(step_description='Fix edge case')           -- adds 1 step
            add_step(step_description=['Fix edge case',          -- adds 2 steps
                                       'Update changelog'])

    complete_task(result)
        Called after git_commit, when the main task is fully done.
        Appends the completion block with the final result and Status: Done.
        All step progress is already tracked live — no need to re-list steps.
        Returns a result string containing TASK_COMPLETE so the graph
        can detect task completion and reset the context window.

File format:

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

    (checkboxes are edited in-place as the agent progresses — no duplication)

    ---
    ## 2025-04-25 14:45 | Task: Create calculator module
    ### Result: Calculator module complete — 8/8 tests passed, committed.
    ### Status: Done
    ---
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Union

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


def _build_initial_steps_block() -> str:
    """Renders all steps as [ ] checkboxes for start_task."""
    return "\n".join(f"- [ ] {desc}" for desc in _current_steps)


def _edit_steps_in_file(todo_path: Path, indices: List[int]) -> None:
    """
    Edits step checkboxes in-place in the ### Plan block.
    For each index, finds the step line and replaces its marker.
    A note is written only for single-step updates.

    Scopes all replacements to the LAST ### Plan block in the file so that
    step descriptions that appear in earlier (completed) tasks are not
    accidentally matched instead of the active task's steps.
    """
    content = todo_path.read_text(encoding="utf-8")

    # Find the start of the last ### Plan block
    plan_matches = list(re.finditer(r"^### Plan$", content, re.MULTILINE))
    if not plan_matches:
        logger.warning("[todo_tools] _edit_steps_in_file: no ### Plan block found in file")
        return
    plan_start = plan_matches[-1].end()  # character index just after "### Plan"

    prefix  = content[:plan_start]   # everything before (and including) ### Plan
    section = content[plan_start:]   # the active plan block + anything after it

    for i in indices:
        desc = _current_steps[i]
        marker = _current_step_statuses[i]
        note = _current_step_notes[i]
        new_line = f"- [{marker}] {desc}"
        if note:
            new_line += f" \u2014 {note}"
        pattern = re.compile(
            r"- \[[x~! ]\] " + re.escape(desc) + r"(?: \u2014 [^\n]*)?",
            re.MULTILINE
        )
        new_section = pattern.sub(new_line, section, count=1)
        if new_section == section:
            logger.warning(f"[todo_tools] _edit_steps_in_file: step {i} '{desc}' not found in active plan block — skipping")
        section = new_section

    todo_path.write_text(prefix + section, encoding="utf-8")


def _append_step_to_plan(todo_path: Path, step_desc: str) -> None:
    """
    Appends a new [ ] step line to the active ### Plan block.
    Inserts the line after the last step line currently in the file.
    """
    content = todo_path.read_text(encoding="utf-8")
    new_line = f"- [ ] {step_desc}"
    matches = list(re.finditer(r"^- \[[x~! ]\] .+$", content, re.MULTILINE))
    if matches:
        last_match = matches[-1]
        insert_pos = last_match.end()
        # Normalize: strip any trailing newlines at the cut point, then add exactly one
        content = content[:insert_pos].rstrip("\n") + "\n" + new_line + "\n" + content[insert_pos:].lstrip("\n")
    else:
        content = content.rstrip() + "\n" + new_line + "\n"
    todo_path.write_text(content, encoding="utf-8")


def _recover_state_from_file() -> bool:
    """
    Recover in-memory task state from project_TODO.md after a process restart.
    Called when _current_task_name is empty but an active task may exist in the file.
    Returns True if recovery succeeded, False if no active task found.
    """
    global _current_task_name, _current_steps, _current_step_statuses
    global _current_step_notes, _task_start_ts

    todo_path = _todo_path()
    if not todo_path.exists():
        return False

    content = todo_path.read_text(encoding="utf-8")

    # Only recover if the last status line says In Progress
    status_lines = re.findall(r"^### Status: (.+)$", content, re.MULTILINE)
    if not status_lines or status_lines[-1].strip() != "In Progress":
        return False

    # Extract task name from the last ## ... | Task: ... header
    header_matches = list(re.finditer(r"^## .+? \| Task: (.+)$", content, re.MULTILINE))
    if not header_matches:
        return False
    _current_task_name = header_matches[-1].group(1).strip()

    # Extract all step lines from the last ### Plan block
    plan_matches = list(re.finditer(r"^### Plan$", content, re.MULTILINE))
    if plan_matches:
        plan_start = plan_matches[-1].end()
        rest = content[plan_start:]
        steps_raw = re.findall(r"^- \[([x~! ])\] (.+?)(?:\s*\u2014\s*(.+))?$", rest, re.MULTILINE)
        _current_steps           = [s[1].strip() for s in steps_raw]
        _current_step_statuses   = [s[0]         for s in steps_raw]
        _current_step_notes      = [s[2].strip() if s[2] else "" for s in steps_raw]
    else:
        _current_steps           = []
        _current_step_statuses   = []
        _current_step_notes      = []

    _task_start_ts = ""
    logger.info(f"[todo_tools] Recovered task '{_current_task_name}' with {len(_current_steps)} steps from file after restart")
    return True


# -- Tool schema ---------------------------------------------------------------

class UpdateProjectTodoInput(BaseModel):
    operation: Literal["start_task", "update_step", "add_step", "complete_task"] = Field(
        description=(
            "Which operation to perform:\n"
            "  start_task    -- FIRST call when beginning a task. Pass task_name + full steps plan.\n"
            "  update_step   -- After finishing, starting, or blocking step(s). Pass step_index + status.\n"
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
    step_index: Union[int, List[int]] = Field(
        default=-1,
        description=(
            "REQUIRED for update_step. 0-based index of the step(s) to update. "
            "Pass a single int to update one step, or a list of ints to bulk-update "
            "multiple steps in one call (all receive the same status). "
            "First step = 0. Ignored for all other operations. "
            "Bulk example: step_index=[0,1,2], status='x' marks 3 steps done in one request."
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
            "Optional for update_step (single step only). "
            "Short context note appended to the step line. "
            "Example: 'fixed deprecated Communicate() call'. "
            "Ignored for bulk updates and all other operations."
        )
    )
    step_description: Union[str, List[str]] = Field(
        default="",
        description=(
            "REQUIRED for add_step. One new step as a string, or a list of strings to add multiple steps at once. "
            "Each step will be added as [ ] (not started). "
            "Example (single): 'Fix missing __init__.py'. "
            "Example (bulk): ['Fix missing __init__.py', 'Update requirements.txt']. "
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
    step_index: Union[int, List[int]] = -1,
    status: str = "",
    note: str = "",
    step_description: Union[str, List[str]] = "",
    result: str = "",
) -> str:
    """
    Manage the project task log (project_TODO.md).

    WHEN TO CALL EACH OPERATION:
    - start_task:    First tool call when you begin executing a task.
                     Pass the full planned step list upfront as [ ] checkboxes.
                     Do NOT call for pure conversations or brainstorming.
    - update_step:   After you finish, start, or get blocked on step(s).
                     Pass step_index (int or list[int]) and status: 'x', '~', or '!'.
                     Bulk example: step_index=[0,1,2], status='x' marks 3 steps done.
                     Checkboxes are edited in-place — no duplicate blocks written.
    - add_step:      When you discover one or more unplanned steps mid-task.
                     Pass a single string or a list of strings.
                     All new steps are appended as [ ] to the existing plan block.
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
            # Guard: block if there is already an active task in the file.
            # A completed task also contains '### Status: In Progress' (in its opening block),
            # so we cannot do a simple string search. Instead we find the LAST status line:
            # if it says 'In Progress', no Done block has been written yet => active task.
            if todo_path.exists():
                file_content = todo_path.read_text(encoding="utf-8")
                status_lines = re.findall(r"^### Status: (.+)$", file_content, re.MULTILINE)
                if status_lines and status_lines[-1].strip() == "In Progress":
                    return (
                        "[update_project_todo: There is already a Task in progress. "
                        "Please call `update_project_todo` with operation='add_step' "
                        "to add more steps to the current task, or complete it first "
                        "with operation='complete_task'.]"
                    )

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

            steps_block = _build_initial_steps_block()
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
                if not _recover_state_from_file():
                    return (
                        "[update_project_todo error: no active task. "
                        "Call start_task before update_step.]"
                    )
            if status not in ("x", "~", "!"):
                return (
                    "[update_project_todo error: status must be 'x' (done), '~' (in progress), "
                    "or '!' (blocked).]"
                )

            # Normalise step_index to a list
            if isinstance(step_index, int):
                indices = [step_index]
            else:
                indices = list(step_index)

            # Validate all indices first
            invalid = [i for i in indices if i < 0 or i >= len(_current_steps)]
            if invalid:
                return (
                    f"[update_project_todo error: step_index {invalid} out of range. "
                    f"Valid range: 0 to {len(_current_steps) - 1}.]"
                )

            # Apply updates to in-memory state
            is_bulk = len(indices) > 1
            for i in indices:
                _current_step_statuses[i] = status
                if note and not is_bulk:
                    _current_step_notes[i] = note.strip()

            # Edit checkboxes in-place in the file
            _edit_steps_in_file(todo_path, indices)

            status_label = {"x": "Done", "~": "In Progress", "!": "Blocked"}.get(status, status)
            if is_bulk:
                logger.info(f"[todo_tools] update_step (bulk): steps {indices} -> [{status}] {status_label}")
                return (
                    f"update_project_todo: {len(indices)} steps updated to [{status}] {status_label}\n"
                    f"Indices: {indices}"
                )
            else:
                i = indices[0]
                logger.info(f"[todo_tools] update_step: step {i} -> [{status}] {status_label}")
                return (
                    f"update_project_todo: Step {i} updated to [{status}] {status_label}\n"
                    f"Step: {_current_steps[i]}\n"
                    f"Note: {note or '(none)'}"
                )

        # -- add_step ----------------------------------------------------------
        elif operation == "add_step":
            if not _current_task_name:
                if not _recover_state_from_file():
                    return (
                        "[update_project_todo error: no active task. "
                        "Call start_task before add_step.]"
                    )
            # Guard: validate before calling .strip() — step_description may be a list
            is_empty = (
                not step_description
                or (isinstance(step_description, str) and not step_description.strip())
                or (isinstance(step_description, list) and not any(s.strip() for s in step_description if isinstance(s, str)))
            )
            if is_empty:
                return (
                    "[update_project_todo error: step_description is required for add_step. "
                    "Example: update_project_todo(operation='add_step', "
                    "step_description='Fix missing __init__.py')]"
                )

            # Normalise to list
            if isinstance(step_description, str):
                new_steps = [step_description.strip()]
            else:
                new_steps = [s.strip() for s in step_description if s.strip()]

            first_index = len(_current_steps)
            for s in new_steps:
                _current_steps.append(s)
                _current_step_statuses.append(" ")
                _current_step_notes.append("")
                _append_step_to_plan(todo_path, s)

            logger.info(f"[todo_tools] add_step: added {len(new_steps)} step(s) starting at index {first_index}")
            return (
                f"update_project_todo: {len(new_steps)} step(s) added starting at index {first_index}\n"
                f"Steps: {new_steps}\n"
                f"Total steps: {len(_current_steps)}"
            )

        # -- complete_task -----------------------------------------------------
        elif operation == "complete_task":
            if not _current_task_name:
                if not _recover_state_from_file():
                    return (
                        "[update_project_todo error: no active task. "
                        "Call start_task before complete_task.]"
                    )
            # Guard: block if any steps in the ACTIVE plan block are still unmarked [ ]
            if todo_path.exists():
                file_content = todo_path.read_text(encoding="utf-8")
                plan_matches = list(re.finditer(r"^### Plan$", file_content, re.MULTILINE))
                active_section = file_content[plan_matches[-1].end():] if plan_matches else file_content
                if re.search(r"- \[ \] ", active_section):
                    return (
                        "[update_project_todo: There are still incomplete steps marked as [ ]. "
                        "Please mark all steps using operation='update_step' "
                        "before calling operation='complete_task'.]"
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
                f"__TASK_COMPLETE__"
            )

        else:
            return (
                f"[update_project_todo error: unknown operation '{operation}'. "
                f"Use 'start_task', 'update_step', 'add_step', or 'complete_task']"
            )

    except Exception as e:
        logger.error(f"[todo_tools] Error in {operation}: {e}", exc_info=True)
        return f"[update_project_todo error: {e}]"
