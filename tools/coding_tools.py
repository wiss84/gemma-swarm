"""
Gemma Swarm — Coding Agent: Layer 1 Workspace Tools
======================================================
All tools the coding agent uses to read, write, and execute files.

Tools:
    read_file(path)                                    — Read a file's content
    write_file(path, content)                          — Write (or overwrite) a file
    edit_file(path, old_text, new_text)                — Replace a unique string in a file
    list_dir(path)                                     — List directory contents as a tree
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
import platform
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from agents_utils.config import BLOCKED_PATTERNS, PROJECT_ROOT

logger = logging.getLogger(__name__)

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
    prefix Python / pytest / pip commands so they run inside gemma_test instead.
    This keeps production dependencies safe.
    """
    active_env = _active_conda_env()
    if active_env != PROD_ENV_NAME:
        return command  # already in test env or outside conda — run directly

    # Commands that should be redirected to the test environment
    test_prefixed_commands = (
        "python ", "python3 ", "pytest ", "pip ", "pip3 ",
        "python\n", "pytest\n",  # edge cases with no args
    )
    for prefix in test_prefixed_commands:
        if command.strip().startswith(prefix.strip()):
            logger.info(f"[execute_shell] Redirecting command to {TEST_ENV_NAME} env")
            return f"conda run -n {TEST_ENV_NAME} {command}"

    return command


def _is_blocked(command: str) -> bool:
    """Return True if the command matches any blocked pattern."""
    lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern.lower() in lower:
            return True
    return False


# ── Tool 1: read_file ──────────────────────────────────────────────────────────

class ReadFileInput(BaseModel):
    path: str = Field(description="Absolute or project-relative path to the file to read.")


@tool(args_schema=ReadFileInput)
def read_file(path: str) -> str:
    """
    Read a file and return its contents as a string.
    Paths can be absolute or relative to the project root.
    Large files are capped at 80,000 characters to protect context window.
    Returns an error string starting with '[' on failure.
    """
    try:
        resolved = Path(path) if Path(path).is_absolute() else PROJECT_ROOT / path
        resolved = resolved.resolve()

        if not resolved.exists():
            return f"[read_file error: File not found: {resolved}]"
        if not resolved.is_file():
            return f"[read_file error: Path is not a file: {resolved}]"

        content = resolved.read_text(encoding="utf-8", errors="replace")

        if len(content) > MAX_READ_CHARS:
            content = content[:MAX_READ_CHARS]
            content += f"\n\n[File truncated at {MAX_READ_CHARS} chars. Use grep_search to find specific sections.]"

        return content

    except Exception as e:
        return f"[read_file error: {e}]"


# ── Tool 2: write_file ─────────────────────────────────────────────────────────

class WriteFileInput(BaseModel):
    path:    str = Field(description="Absolute or project-relative path to write.")
    content: str = Field(description="Full content to write. Overwrites existing file.")


@tool(args_schema=WriteFileInput)
def write_file(path: str, content: str) -> str:
    """
    Write content to a file, creating it and any parent directories if needed.
    Overwrites the file if it already exists.
    Returns a success message or an error string starting with '['.
    """
    try:
        resolved = Path(path) if Path(path).is_absolute() else PROJECT_ROOT / path
        resolved = resolved.resolve()

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

        size = resolved.stat().st_size
        logger.info(f"[write_file] Wrote {size} bytes to {resolved}")
        return f"Successfully wrote {size} bytes to {resolved}"

    except Exception as e:
        return f"[write_file error: {e}]"


# ── Tool 3: edit_file ──────────────────────────────────────────────────────────

class EditFileInput(BaseModel):
    path:     str = Field(description="Absolute or project-relative path to the file to edit.")
    old_text: str = Field(description="The exact string to find. Must appear exactly once in the file.")
    new_text: str = Field(description="The string to replace old_text with.")


@tool(args_schema=EditFileInput)
def edit_file(path: str, old_text: str, new_text: str) -> str:
    """
    Replace a unique string in a file with new content.
    old_text must appear exactly once — this prevents accidental multi-replacement.
    Returns a unified diff of the change, or an error string starting with '['.
    """
    try:
        resolved = Path(path) if Path(path).is_absolute() else PROJECT_ROOT / path
        resolved = resolved.resolve()

        if not resolved.exists():
            return f"[edit_file error: File not found: {resolved}]"

        original = resolved.read_text(encoding="utf-8", errors="replace")
        count    = original.count(old_text)

        if count == 0:
            return f"[edit_file error: old_text not found in {resolved}]"
        if count > 1:
            return (
                f"[edit_file error: old_text appears {count} times in {resolved}. "
                f"Provide more context to make it unique.]"
            )

        updated = original.replace(old_text, new_text, 1)
        resolved.write_text(updated, encoding="utf-8")

        # Build a human-readable diff for confirmation
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{resolved.name}",
            tofile=f"b/{resolved.name}",
            n=3,
        )
        diff_text = "".join(diff)
        if not diff_text:
            diff_text = "(no visible diff — content may be identical)"

        logger.info(f"[edit_file] Edited {resolved}")
        return f"Successfully edited {resolved}\n\nDiff:\n{diff_text}"

    except Exception as e:
        return f"[edit_file error: {e}]"


# ── Tool 4: list_dir ───────────────────────────────────────────────────────────

class ListDirInput(BaseModel):
    path: str = Field(description="Absolute or project-relative path to list.")


@tool(args_schema=ListDirInput)
def list_dir(path: str) -> str:
    """
    List the contents of a directory as a formatted tree.
    Shows files and subdirectories. Skips hidden files (starting with '.'),
    __pycache__, node_modules, and .venv / venv directories.
    Returns an error string starting with '[' on failure.
    """
    SKIP_DIRS  = {"__pycache__", "node_modules", ".venv", "venv", ".git"}
    SKIP_FILES = {".DS_Store", "Thumbs.db"}

    try:
        resolved = Path(path) if Path(path).is_absolute() else PROJECT_ROOT / path
        resolved = resolved.resolve()

        if not resolved.exists():
            return f"[list_dir error: Path not found: {resolved}]"
        if not resolved.is_dir():
            return f"[list_dir error: Not a directory: {resolved}]"

        lines = [f"{resolved}/"]

        def _walk(directory: Path, prefix: str):
            try:
                entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except PermissionError:
                lines.append(f"{prefix}[permission denied]")
                return

            entries = [
                e for e in entries
                if e.name not in SKIP_FILES
                and e.name not in SKIP_DIRS
                and not e.name.startswith(".")
            ]

            for i, entry in enumerate(entries):
                connector = "└── " if i == len(entries) - 1 else "├── "
                if entry.is_dir():
                    lines.append(f"{prefix}{connector}{entry.name}/")
                    extension = "    " if i == len(entries) - 1 else "│   "
                    _walk(entry, prefix + extension)
                else:
                    size  = entry.stat().st_size
                    size_str = f"{size:,} B" if size < 1024 else f"{size / 1024:.1f} KB"
                    lines.append(f"{prefix}{connector}{entry.name}  ({size_str})")

        _walk(resolved, "")
        return "\n".join(lines)

    except Exception as e:
        return f"[list_dir error: {e}]"


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
            base = Path(base_path) if Path(base_path).is_absolute() else PROJECT_ROOT / base_path
        else:
            base = PROJECT_ROOT

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
        resolved = Path(path) if Path(path).is_absolute() else PROJECT_ROOT / path
        resolved = resolved.resolve()

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
    Python/pytest/pip commands are automatically redirected to the gemma_test
    conda environment if currently running inside gemma_swarm.
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

    # Resolve working directory
    if working_dir:
        cwd = Path(working_dir) if Path(working_dir).is_absolute() else PROJECT_ROOT / working_dir
        cwd = cwd.resolve()
    else:
        cwd = PROJECT_ROOT

    if not cwd.exists():
        return f"[execute_shell error: Working directory not found: {cwd}]"

    logger.info(f"[execute_shell] Running: {safe_command!r} in {cwd}")

    try:
        use_shell = platform.system() == "Windows"

        result = subprocess.run(
            safe_command if use_shell else safe_command.split(),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=use_shell,
            env={**os.environ},
        )

        stdout   = result.stdout.strip()
        stderr   = result.stderr.strip()
        exitcode = result.returncode

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
        return f"[execute_shell error: Command timed out after {timeout}s: '{safe_command}']"
    except FileNotFoundError as e:
        return f"[execute_shell error: Command not found: {e}]"
    except Exception as e:
        return f"[execute_shell error: {e}]"
