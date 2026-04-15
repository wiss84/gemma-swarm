"""
Gemma Swarm — Coding Agent: Layer 3 Project Understanding Tools
================================================================
Tools that give the coding agent a full picture of any project before
touching a single file. The agent should call these at the start of
every task to understand what it's working with.

Tools:
    read_project_structure(root_path, max_depth, exclude_dirs)  — directory tree with sizes
    search_codebase(pattern, root_path, file_extensions)        — regex grep across all source files
    read_requirements(root_path)                                — parse requirements.txt / pyproject.toml
    read_git_log(root_path, n_commits)                          — recent commit history

Usage pattern the agent should follow at the start of every task:
    1. read_project_structure(workspace_path)   → understand what files exist
    2. read_requirements(workspace_path)        → know what packages are in play
    3. search_codebase("class|def ", ...)       → find relevant code quickly
    4. read_git_log(workspace_path)             → see recent changes for context
"""

import re
import logging
import platform
import subprocess
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from agents_utils.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_MAX_DEPTH    = 4
MAX_DEPTH_LIMIT      = 8
MAX_CODEBASE_MATCHES = 50
DEFAULT_N_COMMITS    = 10
MAX_N_COMMITS        = 50
MAX_REQUIREMENTS_CHARS = 8_000

SKIP_DIRS = {
    "__pycache__", "node_modules", ".venv", "venv", ".git",
    ".tox", ".pytest_cache", ".mypy_cache", "dist", "build",
    ".eggs", "*.egg-info", ".ruff_cache",
}

DEFAULT_EXTENSIONS = {".py", ".js", ".ts", ".md", ".txt", ".yaml", ".yml", ".json", ".toml"}


# ── Internal helpers ───────────────────────────────────────────────────────────

def _resolve_path(path: str) -> Path:
    """Resolve a path — absolute or relative to project root."""
    p = Path(path)
    return p.resolve() if p.is_absolute() else (PROJECT_ROOT / path).resolve()


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,  # always pass as list to avoid shell interpretation issues
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "git command timed out"
    except FileNotFoundError:
        return -1, "", "git not found in PATH"
    except Exception as e:
        return -1, "", str(e)


# ── Tool 1: read_project_structure ────────────────────────────────────────────

class ReadProjectStructureInput(BaseModel):
    root_path:    str = Field(default="", description="Root directory to scan. Defaults to project root.")
    max_depth:    int = Field(default=DEFAULT_MAX_DEPTH, description=f"Max directory depth. Default {DEFAULT_MAX_DEPTH}, max {MAX_DEPTH_LIMIT}.")
    exclude_dirs: list[str] = Field(default_factory=list, description="Additional directory names to exclude beyond defaults.")


@tool(args_schema=ReadProjectStructureInput)
def read_project_structure(root_path: str = "", max_depth: int = DEFAULT_MAX_DEPTH, exclude_dirs: list[str] = None) -> str:
    """
    Walk a directory tree and return a formatted structure showing all files
    and directories, with file sizes. Skips common noise directories
    (__pycache__, .git, node_modules, .venv, dist, build, etc.).
    Call this at the start of a task to understand the project layout.
    Returns an error string starting with '[' on failure.
    """
    if exclude_dirs is None:
        exclude_dirs = []

    skip = SKIP_DIRS | set(exclude_dirs)
    max_depth = min(max(1, max_depth), MAX_DEPTH_LIMIT)

    try:
        root = _resolve_path(root_path) if root_path else PROJECT_ROOT

        if not root.exists():
            return f"[read_project_structure error: Path not found: {root}]"
        if not root.is_dir():
            return f"[read_project_structure error: Not a directory: {root}]"

        lines  = [f"{root}/"]
        counts = {"files": 0, "dirs": 0}

        def _walk(directory: Path, prefix: str, depth: int):
            if depth > max_depth:
                lines.append(f"{prefix}... (max depth {max_depth} reached)")
                return
            try:
                entries = sorted(
                    directory.iterdir(),
                    key=lambda p: (p.is_file(), p.name.lower()),
                )
            except PermissionError:
                lines.append(f"{prefix}[permission denied]")
                return

            # Filter: skip noise dirs and hidden entries
            entries = [
                e for e in entries
                if e.name not in skip
                and not e.name.startswith(".")
                and not any(e.name.endswith(s.lstrip("*")) for s in skip if "*" in s)
            ]

            for i, entry in enumerate(entries):
                connector  = "└── " if i == len(entries) - 1 else "├── "
                child_pfx  = prefix + ("    " if i == len(entries) - 1 else "│   ")

                if entry.is_dir():
                    counts["dirs"] += 1
                    lines.append(f"{prefix}{connector}{entry.name}/")
                    _walk(entry, child_pfx, depth + 1)
                else:
                    counts["files"] += 1
                    try:
                        size = entry.stat().st_size
                        size_str = f"{size:,} B" if size < 1024 else f"{size / 1024:.1f} KB"
                    except OSError:
                        size_str = "?"
                    lines.append(f"{prefix}{connector}{entry.name}  ({size_str})")

        _walk(root, "", 1)
        lines.append(f"\n{counts['dirs']} directories, {counts['files']} files")
        return "\n".join(lines)

    except Exception as e:
        return f"[read_project_structure error: {e}]"


# ── Tool 2: search_codebase ───────────────────────────────────────────────────

class SearchCodebaseInput(BaseModel):
    pattern:         str = Field(description="Regex pattern to search for, e.g. 'class BaseAgent' or 'def run'.")
    root_path:       str = Field(default="", description="Root directory to search. Defaults to project root.")
    file_extensions: list[str] = Field(
        default_factory=list,
        description="File extensions to include, e.g. ['.py', '.md']. Defaults to common source file types."
    )


@tool(args_schema=SearchCodebaseInput)
def search_codebase(pattern: str, root_path: str = "", file_extensions: list[str] = None) -> str:
    """
    Recursively search all source files for a regex pattern.
    Returns matching lines with file path and line number.
    Capped at 50 matches to avoid flooding context — narrow your pattern if needed.
    Use this to find where classes, functions, or variables are defined across the project.
    Returns an error string starting with '[' on failure.
    """
    if file_extensions is None:
        file_extensions = []

    extensions = set(file_extensions) if file_extensions else DEFAULT_EXTENSIONS

    try:
        root = _resolve_path(root_path) if root_path else PROJECT_ROOT

        if not root.exists():
            return f"[search_codebase error: Path not found: {root}]"

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"[search_codebase error: Invalid regex '{pattern}': {e}]"

        # Collect all matching files
        all_files = []
        for ext in extensions:
            all_files.extend(
                f for f in root.rglob(f"*{ext}")
                if f.is_file()
                and not any(part in SKIP_DIRS or part.startswith(".") for part in f.parts)
            )
        all_files = sorted(set(all_files))

        matches   = []
        truncated = False

        for file in all_files:
            try:
                lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue

            for lineno, line in enumerate(lines, 1):
                if regex.search(line):
                    # Show path relative to root for readability
                    try:
                        rel = file.relative_to(root)
                    except ValueError:
                        rel = file
                    matches.append(f"{rel}:{lineno}: {line.rstrip()}")
                    if len(matches) >= MAX_CODEBASE_MATCHES:
                        truncated = True
                        break
            if truncated:
                break

        if not matches:
            return (
                f"No matches found for '{pattern}' in {root}\n"
                f"(searched {len(all_files)} files with extensions: {sorted(extensions)})"
            )

        result = f"Found {len(matches)} match(es) for '{pattern}' in {root}:\n\n"
        result += "\n".join(matches)
        if truncated:
            result += f"\n\n[Output capped at {MAX_CODEBASE_MATCHES} matches. Use a more specific pattern.]"

        return result

    except Exception as e:
        return f"[search_codebase error: {e}]"


# ── Tool 3: read_requirements ─────────────────────────────────────────────────

class ReadRequirementsInput(BaseModel):
    root_path: str = Field(default="", description="Project root to search for requirements files. Defaults to project root.")


@tool(args_schema=ReadRequirementsInput)
def read_requirements(root_path: str = "") -> str:
    """
    Parse and return the project's dependency list from requirements.txt
    and/or pyproject.toml. Returns a clean, structured view of all
    declared dependencies and their version constraints.
    Call this before writing any code that installs or uses packages —
    you need to know what's already declared before adding new deps.
    Returns an error string starting with '[' on failure.
    """
    try:
        root = _resolve_path(root_path) if root_path else PROJECT_ROOT

        if not root.exists():
            return f"[read_requirements error: Path not found: {root}]"

        sections = []

        # ── requirements.txt ──────────────────────────────────────────────────
        req_file = root / "requirements.txt"
        if req_file.exists():
            content = req_file.read_text(encoding="utf-8", errors="replace")
            if len(content) > MAX_REQUIREMENTS_CHARS:
                content = content[:MAX_REQUIREMENTS_CHARS] + "\n...[truncated]"

            lines   = content.splitlines()
            deps    = []
            current_section = None

            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    # Use comment headers as section names
                    header = stripped.lstrip("#").strip()
                    if header:
                        current_section = header
                    continue
                if stripped.startswith("-r ") or stripped.startswith("--"):
                    deps.append(f"  {stripped}  (include/flag)")
                    continue
                # Parse package==version or package>=version etc.
                deps.append(f"  {stripped}")

            if deps:
                block = "requirements.txt:\n"
                if current_section:
                    block += f"  (last section: {current_section})\n"
                block += "\n".join(deps)
                sections.append(block)

        # ── pyproject.toml ────────────────────────────────────────────────────
        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(encoding="utf-8", errors="replace")
            if len(content) > MAX_REQUIREMENTS_CHARS:
                content = content[:MAX_REQUIREMENTS_CHARS] + "\n...[truncated]"

            # Extract [project.dependencies] or [tool.poetry.dependencies] section
            dep_lines   = []
            in_deps     = False
            section_pat = re.compile(r"^\[(.+)\]")
            dep_sections = {
                "project.dependencies",
                "tool.poetry.dependencies",
                "tool.poetry.dev-dependencies",
                "project.optional-dependencies",
            }

            for line in content.splitlines():
                m = section_pat.match(line.strip())
                if m:
                    in_deps = m.group(1).lower() in dep_sections
                    if in_deps:
                        dep_lines.append(f"\n  [{m.group(1)}]")
                    continue
                if in_deps and line.strip() and not line.strip().startswith("["):
                    dep_lines.append(f"  {line.strip()}")

            if dep_lines:
                sections.append("pyproject.toml dependencies:" + "\n".join(dep_lines))

        # ── Nothing found ─────────────────────────────────────────────────────
        if not sections:
            return (
                f"[read_requirements: No requirements.txt or pyproject.toml found in {root}. "
                f"This project may not have a standard Python dependency file.]"
            )

        header = f"Dependencies for project at: {root}\n" + "─" * 60 + "\n\n"
        return header + "\n\n".join(sections)

    except Exception as e:
        return f"[read_requirements error: {e}]"


# ── Tool 4: read_git_log ──────────────────────────────────────────────────────

class ReadGitLogInput(BaseModel):
    root_path: str = Field(default="", description="Path to the git repository root. Defaults to project root.")
    n_commits: int = Field(default=DEFAULT_N_COMMITS, description=f"Number of recent commits to return. Default {DEFAULT_N_COMMITS}, max {MAX_N_COMMITS}.")


@tool(args_schema=ReadGitLogInput)
def read_git_log(root_path: str = "", n_commits: int = DEFAULT_N_COMMITS) -> str:
    """
    Return the recent git commit history for a repository.
    Shows commit hash, author, date, and message for the last N commits.
    Use this to understand what has recently changed before editing files,
    and to find the right branch/commit to base new work on.
    Returns an error string starting with '[' if the path is not a git repo.
    """
    n_commits = min(max(1, n_commits), MAX_N_COMMITS)

    try:
        root = _resolve_path(root_path) if root_path else PROJECT_ROOT

        if not root.exists():
            return f"[read_git_log error: Path not found: {root}]"

        # Verify it's actually a git repo
        rc, _, err = _run_git(["rev-parse", "--git-dir"], root)
        if rc != 0:
            return (
                f"[read_git_log: {root} is not a git repository or git is not installed. "
                f"Error: {err}]"
            )

        # Get current branch
        rc_branch, branch, _ = _run_git(["branch", "--show-current"], root)
        current_branch = branch if rc_branch == 0 and branch else "unknown"

        # Get formatted log
        # Format: hash | author | relative date | subject
        fmt = "%h | %an | %ar | %s"
        rc_log, log_out, log_err = _run_git(
            ["log", f"--pretty=format:{fmt}", f"-{n_commits}"],
            root,
        )

        if rc_log != 0:
            return f"[read_git_log error: git log failed: {log_err}]"

        if not log_out:
            return f"[read_git_log: No commits found in {root}]"

        # Also get status summary
        rc_st, status_out, _ = _run_git(["status", "--short"], root)
        status_summary = ""
        if rc_st == 0 and status_out:
            changed_count = len(status_out.splitlines())
            status_summary = f"\nUncommitted changes: {changed_count} file(s) modified/staged"

        lines = log_out.splitlines()
        header = (
            f"Git log for: {root}\n"
            f"Current branch: {current_branch}{status_summary}\n"
            f"Last {len(lines)} commit(s):\n"
            + "─" * 60 + "\n"
        )
        formatted = "\n".join(f"  {line}" for line in lines)
        return header + formatted

    except Exception as e:
        return f"[read_git_log error: {e}]"
