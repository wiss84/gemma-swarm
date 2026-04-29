"""
Gemma Swarm — Coding Agent: Layer 1 Workspace Tools
======================================================
All tools the coding agent uses to read, write, and execute files.

Tools:
    read_files(paths)                                  — Read multiple files at once (up to 10)
    write_files(files)                                 — Write multiple files at once (up to 10)
    edit_files(edits)                                  — Apply multiple edits across files (up to 20)
    glob_search(pattern, base_path)                    — Find files matching a glob pattern
    grep_search(pattern, path, file_pattern)           — Search file(s) for a regex pattern
    execute_shell(command, working_dir, timeout)       — Run a shell command safely

Environment safety:
    All shell commands are checked against BLOCKED_PATTERNS from config.
    execute_shell auto-detects the active conda env and redirects Python/pytest
    commands to gemma_test if currently running inside gemma_swarm.
"""

import os
import re
import subprocess
import difflib
import logging
import threading
from queue import Queue, Empty
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from agents_utils.config import BLOCKED_PATTERNS, PROJECT_ROOT
from agents_utils.get_test_env import get_gemma_test_python_exe

logger = logging.getLogger(__name__)

# ── Active cancel event (set per coding session) ──────────────────────────────
# Set by the coding agent before each tool loop so execute_shell can kill the
# subprocess immediately when the Stop button is clicked, rather than waiting
# for the full timeout.
_active_cancel_event: threading.Event | None = None


def set_shell_cancel_event(event: threading.Event | None):
    """Register the active session's cancel event so execute_shell can abort early."""
    global _active_cancel_event
    _active_cancel_event = event


# ── Workspace root (overridden per coding session) ─────────────────────────────
# Set by the coding agent at session start via set_coding_workspace_root().
# All tools use this as their default path instead of PROJECT_ROOT,
# so the agent can only access files within its assigned workspace.

_CODING_WORKSPACE_ROOT: Path | None = None


def set_coding_workspace_root(path: str):
    """Called once per coding session. All tool path defaults point here."""
    global _CODING_WORKSPACE_ROOT
    _CODING_WORKSPACE_ROOT = Path(path).resolve() if path else None


def _workspace_root() -> Path:
    """Return the active workspace root, falling back to PROJECT_ROOT."""
    return _CODING_WORKSPACE_ROOT if _CODING_WORKSPACE_ROOT else PROJECT_ROOT


def _is_within_workspace(resolved_path: Path, workspace_root: Path) -> bool:
    """
    Check if resolved_path is within workspace_root or its subdirectories.
    Returns False if the path attempts to escape the workspace via .. or symlinks.
    """
    try:
        resolved_path.relative_to(workspace_root)
        return True
    except ValueError:
        return False


def _resolve_tool_path(path: str) -> Path:
    """
    Resolve a path for tool use:
    - Absolute paths are validated to be within the workspace.
    - Relative paths are resolved against the active workspace root.
    Raises ValueError if the resolved path escapes the workspace boundary.
    """
    workspace_root = _workspace_root()
    p = Path(path)
    
    if p.is_absolute():
        resolved = p.resolve()
    else:
        resolved = (workspace_root / path).resolve()
    
    # Validate path is within workspace
    if not _is_within_workspace(resolved, workspace_root):
        raise ValueError(
            f"Access denied: path is outside your workspace. "
            f"Attempted: {resolved} | Workspace: {workspace_root}"
        )
    
    return resolved

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_SHELL_TIMEOUT = 30
MAX_SHELL_TIMEOUT     = 120
MAX_READ_CHARS        = 80_000   # cap large files to avoid flooding context
MAX_GREP_MATCHES      = 100
PROD_ENV_NAME         = "gemma_swarm"
TEST_ENV_NAME         = "gemma_test"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _active_conda_env() -> str:
    """Return the name of the active conda environment, or '' if not in one."""
    return os.environ.get("CONDA_DEFAULT_ENV", "")

def _prefix_for_test_env(command: str) -> str:
    """
    If we are running inside the production environment (gemma_swarm),
    redirect Python / pytest / pip commands to use the absolute path to
    the gemma_test Python interpreter instead of relying on conda activation.
    This keeps production dependencies safe and works reliably on Windows.

    Mapping:
        python <args>   →  <py_exe> <args>          (direct replacement)
        python3 <args>  →  <py_exe> <args>
        pytest <args>   →  <py_exe> -m pytest <args>
        pip <args>      →  <py_exe> -m pip <args>
        pip3 <args>     →  <py_exe> -m pip <args>
    """
    active_env = _active_conda_env()
    if active_env != PROD_ENV_NAME:
        return command  # already in test env or outside conda — run directly

    py_exe = get_gemma_test_python_exe()
    # Do NOT quote py_exe here. execute_shell runs with shell=True on all platforms,
    # so the shell handles any spaces in the path. Embedding manual quotes breaks
    # paths on Mac/Linux where the shell doesn't strip them.
    stripped = command.strip()

    redirects = [
        ("python3 ", lambda rest: f"{py_exe} {rest}"),
        ("python3",  lambda rest: f"{py_exe}{rest}"),
        ("python ",  lambda rest: f"{py_exe} {rest}"),
        ("python",   lambda rest: f"{py_exe}{rest}"),
        ("pytest ",  lambda rest: f"{py_exe} -m pytest {rest}"),
        ("pytest",   lambda rest: f"{py_exe} -m pytest{rest}"),
        ("pip3 ",    lambda rest: f"{py_exe} -m pip {rest}"),
        ("pip3",     lambda rest: f"{py_exe} -m pip{rest}"),
        ("pip ",     lambda rest: f"{py_exe} -m pip {rest}"),
        ("pip",      lambda rest: f"{py_exe} -m pip{rest}"),
    ]

    for prefix, build in redirects:
        if stripped.startswith(prefix):
            rest = stripped[len(prefix):]
            redirected = build(rest)
            logger.info(
                f"[execute_shell] Redirecting to {TEST_ENV_NAME}: "
                f"{prefix.strip()!r} → {redirected[:80]}"
            )
            return redirected

    return command


def _is_blocked(command: str) -> bool:
    """Return True if the command matches any blocked pattern."""
    lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern.lower() in lower:
            return True
    return False

# ── Tool 5: glob_search ────────────────────────────────────────────────────────

class GlobSearchInput(BaseModel):
    pattern:   str = Field(description="Glob pattern, e.g. '**/*.py' or 'tools/*.py'")
    base_path: str = Field(default="", description="Base directory to search from. Defaults to project root.")


@tool(args_schema=GlobSearchInput)
def glob_search(pattern: str, base_path: str = "") -> str:
    """
    Find files matching a glob pattern relative to base_path (or project root).
    Returns a newline-separated list of matching paths.
    Example patterns: '**/*.py', 'tools/*.py', 'tests/test_*.py'
    Returns an error string starting with '[' on failure.
    """
    try:
        if base_path:
            base = _resolve_tool_path(base_path)
        else:
            base = _workspace_root()

        base = base.resolve()

        if not base.exists():
            return f"[glob_search error: Base path not found: {base}]"

        matches = sorted(base.glob(pattern))

        # Filter out pycache and hidden files
        matches = [
            m for m in matches
            if "__pycache__" not in m.parts
            and not any(part.startswith(".") for part in m.parts)
        ]

        if not matches:
            return f"No files matched pattern '{pattern}' in {base}"

        lines = [f"Found {len(matches)} match(es) for '{pattern}' in {base}:\n"]
        lines += [str(m) for m in matches]
        return "\n".join(lines)

    except Exception as e:
        return f"[glob_search error: {e}]"


# ── Tool 6: grep_search ────────────────────────────────────────────────────────

class GrepSearchInput(BaseModel):
    pattern:      str = Field(description="Regex pattern to search for.")
    path:         str = Field(description="File or directory to search in.")
    file_pattern: str = Field(default="*.py", description="Glob pattern to filter files when path is a directory. Default: '*.py'")


@tool(args_schema=GrepSearchInput)
def grep_search(pattern: str, path: str, file_pattern: str = "*.py") -> str:
    """
    Search for a regex pattern across files. Returns matching lines with file:line numbers.
    When path is a directory, searches all files matching file_pattern recursively.
    Caps output at 100 matches to avoid flooding context.
    Returns an error string starting with '[' on failure.
    """
    try:
        resolved = _resolve_tool_path(path)

        if not resolved.exists():
            return f"[grep_search error: Path not found: {resolved}]"

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"[grep_search error: Invalid regex '{pattern}': {e}]"

        # Build file list
        if resolved.is_file():
            files = [resolved]
        else:
            files = [
                f for f in resolved.rglob(file_pattern)
                if f.is_file()
                and "__pycache__" not in f.parts
                and not any(part.startswith(".") for part in f.parts)
            ]

        matches   = []
        truncated = False

        for file in sorted(files):
            try:
                lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue

            for lineno, line in enumerate(lines, 1):
                if regex.search(line):
                    matches.append(f"{file}:{lineno}: {line.rstrip()}")
                    if len(matches) >= MAX_GREP_MATCHES:
                        truncated = True
                        break
            if truncated:
                break

        if not matches:
            return f"No matches found for pattern '{pattern}' in {resolved}"

        result = f"Found {len(matches)} match(es) for '{pattern}':\n\n"
        result += "\n".join(matches)
        if truncated:
            result += f"\n\n[Output capped at {MAX_GREP_MATCHES} matches. Narrow your search.]"

        return result

    except Exception as e:
        return f"[grep_search error: {e}]"


# ── Tool 7: execute_shell ──────────────────────────────────────────────────────

class ExecuteShellInput(BaseModel):
    command:     str = Field(description="Shell command to execute.")
    working_dir: str = Field(default="", description="Working directory. Defaults to project root.")
    timeout:     int = Field(default=DEFAULT_SHELL_TIMEOUT, description=f"Timeout in seconds. Max {MAX_SHELL_TIMEOUT}.")


@tool(args_schema=ExecuteShellInput)
def execute_shell(command: str, working_dir: str = "", timeout: int = DEFAULT_SHELL_TIMEOUT) -> str:
    """
    Execute a shell command and return its output (stdout + stderr) and exit code.
    Blocked patterns (rm -rf, format c:, etc.) are rejected immediately.
    Timeout is capped at 120 seconds.
    Returns output + exit code, or an error string starting with '['.
    """
    # Safety check
    if _is_blocked(command):
        return f"[execute_shell blocked: Command matches a blocked pattern: '{command}']"

    # Cap timeout
    timeout = min(max(1, timeout), MAX_SHELL_TIMEOUT)

    # Redirect to test env if needed
    safe_command = _prefix_for_test_env(command)

    # Resolve working directory — defaults to active workspace root
    if working_dir:
        cwd = _resolve_tool_path(working_dir)
    else:
        cwd = _workspace_root()

    if not cwd.exists():
        return f"[execute_shell error: Working directory not found: {cwd}]"

    logger.info(f"[execute_shell] Running: {safe_command!r} in {cwd}")

    try:
        # Always use shell=True: safe_command is a string (not a list), shell handles
        # spaces in paths correctly on all platforms, and BLOCKED_PATTERNS guards safety.
        proc = subprocess.Popen(
            safe_command,
            cwd=str(cwd),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ},
        )

        # Queues to collect output from background drain threads
        stdout_queue = Queue()
        stderr_queue = Queue()

        def _drain_pipe(pipe, queue):
            """Read lines from a pipe and put them into a queue until EOF."""
            for line in iter(pipe.readline, b''):
                queue.put(line)
            pipe.close()

        # Start daemon threads to drain stdout/stderr continuously
        threading.Thread(target=_drain_pipe, args=(proc.stdout, stdout_queue), daemon=True).start()
        threading.Thread(target=_drain_pipe, args=(proc.stderr, stderr_queue), daemon=True).start()

        # Poll every 0.2s so a cancel event kills the process almost immediately
        # instead of waiting for the full timeout.
        elapsed = 0.0
        poll_interval = 0.2
        cancelled = False
        while elapsed < timeout:
            ret = proc.poll()
            if ret is not None:
                break  # process finished naturally
            cancel = _active_cancel_event
            if cancel and cancel.is_set():
                proc.kill()
                proc.wait()
                cancelled = True
                break
            threading.Event().wait(poll_interval)
            elapsed += poll_interval
        else:
            # Timeout reached without finishing
            proc.kill()
            proc.wait()

        if cancelled:
            return "[execute_shell cancelled]"

        if proc.poll() is None:
            # Shouldn't happen, but guard
            proc.kill()
            proc.wait()

        # Collect all drained output from queues
        stdout_chunks = []
        stderr_chunks = []

        while True:
            try:
                stdout_chunks.append(stdout_queue.get_nowait())
            except Empty:
                break

        while True:
            try:
                stderr_chunks.append(stderr_queue.get_nowait())
            except Empty:
                break

        stdout_bytes = b''.join(stdout_chunks)
        stderr_bytes = b''.join(stderr_chunks)

        stdout = (stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else "").strip()
        stderr = (stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else "").strip()
        exitcode = proc.returncode

        parts = [f"Exit code: {exitcode}"]
        if stdout:
            parts.append(f"stdout:\n{stdout}")
        if stderr:
            parts.append(f"stderr:\n{stderr}")
        if not stdout and not stderr:
            parts.append("(no output)")

        output = "\n\n".join(parts)
        logger.info(f"[execute_shell] Exit {exitcode}: {safe_command[:60]}")
        return output

    except subprocess.TimeoutExpired:
        return f"[execute_shell error: Command timed out after {timeout}s: '{safe_command}']"  # fallback, should not be reached
    except FileNotFoundError as e:
        return f"[execute_shell error: Command not found: {e}]"
    except Exception as e:
        return f"[execute_shell error: {e}]"


# ── Tool 8: read_files ─────────────────────────────────────────────────────────

class ReadFilesInput(BaseModel):
    paths: list[str] = Field(
        description="List of file paths to read (absolute or project-relative). Up to 10 files at once."
    )


@tool(args_schema=ReadFilesInput)
def read_files(paths: list[str]) -> str:
    """
    Read a single or multiple files in a single call and return all their contents.
    Returns each file's content under a clear header, or an inline error for
    any file that cannot be read (other files still return normally).
    Up to 10 paths per call.
    """
    if not paths:
        return "[read_files error: paths list is empty]"
    if len(paths) > 10:
        return "[read_files error: too many paths — max 10 per call]"

    sections = []
    for path in paths:
        try:
            resolved = _resolve_tool_path(path)
            if not resolved.exists():
                sections.append(f"File: {path}\n[error: file not found: {resolved}]\n")
                continue
            if not resolved.is_file():
                sections.append(f"File: {path}\n[error: not a file: {resolved}]\n")
                continue
            content = resolved.read_text(encoding="utf-8", errors="replace")
            if len(content) > MAX_READ_CHARS:
                content = content[:MAX_READ_CHARS] + f"\n\n[truncated at {MAX_READ_CHARS} chars]"
            sections.append(f"File: {path}\nContent:\n{content}\n")
        except Exception as e:
            sections.append(f"File: {path}\n[error reading file: {e}]\n")

    return ("\n" + "─" * 60 + "\n").join(sections)


# ── Tool 9: write_files ────────────────────────────────────────────────────────

class WriteFilesEntry(BaseModel):
    path:    str = Field(description="File path to write (absolute or project-relative).")
    content: str = Field(description="Full content to write to this file.")


class WriteFilesInput(BaseModel):
    files: list[WriteFilesEntry] = Field(
        description="List of {path, content} pairs. Each file is written (created or overwritten). Up to 10 files at once."
    )


@tool(args_schema=WriteFilesInput)
def write_files(files: list[WriteFilesEntry]) -> str:
    """
    Write a single or multiple files in a single call.
    Use this when you need to create or overwrite a single or several files at once — saves one LLM turn per extra file.
    Each entry needs a path and full content. Parent directories are created
    automatically. Up to 10 files per call.
    Returns a per-file success/error report.
    """
    if not files:
        return "[write_files error: files list is empty]"
    if len(files) > 10:
        return "[write_files error: too many files — max 10 per call]"

    results = []
    for entry in files:
        try:
            resolved = _resolve_tool_path(entry.path)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(entry.content, encoding="utf-8")
            size = resolved.stat().st_size
            logger.info(f"[write_files] Wrote {size} bytes to {resolved}")
            results.append(f"  ✓ {entry.path}  ({size:,} bytes)")
        except Exception as e:
            results.append(f"  ✗ {entry.path}  → error: {e}")

    summary = f"write_files: {len(files)} file(s) processed\n" + "\n".join(results)
    return summary


# ── Tool 10: edit_files ────────────────────────────────────────────────────────

class EditFilesEntry(BaseModel):
    path:     str = Field(description="File path to edit (absolute or project-relative).")
    old_text: str = Field(description="Exact string to replace — must appear exactly once in the file.")
    new_text: str = Field(description="String to replace old_text with.")


class EditFilesInput(BaseModel):
    edits: list[EditFilesEntry] = Field(
        description="List of {path, old_text, new_text} edits. Multiple edits to the same file are applied in order. Up to 20 edits at once."
    )


@tool(args_schema=EditFilesInput)
def edit_files(edits: list[EditFilesEntry]) -> str:
    """
    Apply multiple find-and-replace edits across one or more files in a single call.
    Each edit needs path, old_text (must appear exactly once), and new_text.
    Multiple edits to the same file are applied sequentially in the order given.
    Up to 20 edits per call. Returns a per-edit diff or error report.
    """
    if not edits:
        return "[edit_files error: edits list is empty]"
    if len(edits) > 20:
        return "[edit_files error: too many edits — max 20 per call]"

    # Cache file contents so multiple edits to the same file are applied in-memory
    # before writing, rather than re-reading from disk between edits.
    file_cache: dict[str, str] = {}   # resolved path str → current content
    results = []

    for entry in edits:
        try:
            resolved = _resolve_tool_path(entry.path)
            path_key = str(resolved)

            if path_key not in file_cache:
                if not resolved.exists():
                    results.append(f"  ✗ {entry.path}: file not found")
                    continue
                file_cache[path_key] = resolved.read_text(encoding="utf-8", errors="replace")

            original = file_cache[path_key]
            count = original.count(entry.old_text)

            if count == 0:
                results.append(f"  ✗ {entry.path}: old_text not found")
                continue
            if count > 1:
                results.append(f"  ✗ {entry.path}: old_text appears {count} times — must be unique")
                continue

            updated = original.replace(entry.old_text, entry.new_text, 1)
            file_cache[path_key] = updated

            # Build a minimal diff for confirmation
            diff = difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{resolved.name}",
                tofile=f"b/{resolved.name}",
                n=2,
            )
            diff_text = "".join(diff) or "(no visible diff)"
            results.append(f"  ✓ {entry.path}\n{diff_text}")

        except Exception as e:
            results.append(f"  ✗ {entry.path}: error — {e}")

    # Flush cache to disk
    for path_key, content in file_cache.items():
        try:
            Path(path_key).write_text(content, encoding="utf-8")
        except Exception as e:
            results.append(f"  ✗ write error for {path_key}: {e}")
            logger.error(f"[edit_files] Failed to write {path_key}: {e}")

    summary = f"edit_files: {len(edits)} edit(s) applied\n" + "\n".join(results)
    logger.info(f"[edit_files] Applied {len(edits)} edits across {len(file_cache)} file(s)")
    return summary
