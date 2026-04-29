"""
Gemma Swarm — Coding Agent: Layer 3 Project Understanding Tools
================================================================
Tools that give the coding agent a full picture of any project before
touching a single file. The agent should call these at the start of
every task to understand what it's working with.

Tools:
    read_project_structure(root_path, max_depth, exclude_dirs)  — directory tree with sizes
    search_codebase(pattern, root_path, file_extensions)        — regex grep across all source files
    read_requirements(root_path)                                — parse requirements.txt / pyproject.toml etc.
    read_git_log(root_path, n_commits)                          — recent commit history

Usage pattern the agent should follow at the start of every task:
    1. read_project_structure(workspace_path)   → understand what files exist
    2. read_requirements(workspace_path)        → know what packages are in play
    3. search_codebase("class|def ", ...)       → find relevant code quickly
    4. read_git_log(workspace_path)             → see recent changes for context
"""

import re
import json
import logging
import os
import subprocess
import threading
from queue import Queue, Empty
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from tools.coding_tools import _workspace_root, _resolve_tool_path as _resolve_path

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

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

# _resolve_path is imported from coding_tools above


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr). Safe from deadlocks."""
    cmd = ["git"] + args
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ},
        )

        stdout_queue = Queue()
        stderr_queue = Queue()

        def _drain_pipe(pipe, queue):
            for line in iter(pipe.readline, b''):
                queue.put(line)
            pipe.close()

        threading.Thread(target=_drain_pipe, args=(proc.stdout, stdout_queue), daemon=True).start()
        threading.Thread(target=_drain_pipe, args=(proc.stderr, stderr_queue), daemon=True).start()

        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

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

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        return proc.returncode, stdout.strip(), stderr.strip()

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
        root = _resolve_path(root_path) if root_path else _workspace_root()

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
                        # Add character count for text-like files so agent knows if file is too large
                        # (helps determine if subagent should be spawned for files > 80k chars)
                        char_str = ""
                        if entry.suffix in (".py", ".js", ".ts", ".md", ".txt",
                                            ".yaml", ".yml", ".json", ".toml",
                                            ".html", ".css", ".sh", ".env"):
                            try:
                                content = entry.read_text(encoding="utf-8", errors="replace")
                                char_count = len(content)
                                char_str = f", {char_count:,} chars"
                            except Exception:
                                pass
                        lines.append(f"{prefix}{connector}{entry.name}  ({size_str}{char_str})")
                    except OSError:
                        lines.append(f"{prefix}{connector}{entry.name}")

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
        root = _resolve_path(root_path) if root_path else _workspace_root()

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
    root_path: str = Field(default="", description="Project root to search for dependency files (requirements.txt, pyproject.toml, package.json, etc.). Defaults to project root.")


@tool(args_schema=ReadRequirementsInput)
def read_requirements(root_path: str = "") -> str:
    """
    Parse and return the project's dependency list from Python AND JS/TS
    dependency files (requirements.txt, pyproject.toml, package.json, etc.).
    Returns a clean, structured view of all declared dependencies and their
    version constraints, clearly labeled by language and source file.
    Call this before writing any code that installs or uses packages —
    you need to know what's already declared before adding new deps.
    Call with no arguments to scan the entire workspace, or provide a
    specific root_path to narrow the search.
    Returns an error string starting with '[' on failure.
    """
    try:
        root = _resolve_path(root_path) if root_path else _workspace_root()

        if not root.exists():
            return f"[read_requirements error: Path not found: {root}]"

        sections = []

        # Scan root and all subdirectories (skip noise dirs) for dependency files.
        _skip = {"__pycache__", "node_modules", ".venv", "venv", ".git",
                 ".tox", ".pytest_cache", ".mypy_cache", "dist", "build"}

        req_files       = sorted(p for p in root.rglob("requirements*.txt")
                                   if not any(part in _skip for part in p.parts))
        pyproject_files = sorted(p for p in root.rglob("pyproject.toml")
                                  if not any(part in _skip for part in p.parts))
        pipfile_files    = sorted(p for p in root.rglob("Pipfile")
                                  if not any(part in _skip for part in p.parts))
        setup_files     = sorted(p for p in root.rglob("setup.py")
                                  if not any(part in _skip for part in p.parts))
        setup_cfg_files = sorted(p for p in root.rglob("setup.cfg")
                                  if not any(part in _skip for part in p.parts))

        def _parse_req_txt(req_file: Path) -> str | None:
            content = req_file.read_text(encoding="utf-8", errors="replace")
            if len(content) > MAX_REQUIREMENTS_CHARS:
                content = content[:MAX_REQUIREMENTS_CHARS] + "\n...[truncated]"
            deps, current_section = [], None
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    header = stripped.lstrip("#").strip()
                    if header:
                        current_section = header
                    continue
                if stripped.startswith("-r ") or stripped.startswith("--"):
                    deps.append(f"  {stripped}  (include/flag)")
                    continue
                deps.append(f"  {stripped}")
            if not deps:
                return None
            try:
                rel = req_file.relative_to(root)
            except ValueError:
                rel = req_file
            block = f"requirements.txt ({rel}):\n"
            if current_section:
                block += f"  (last section: {current_section})\n"
            block += "\n".join(deps)
            return block

        for req_file in req_files:
            block = _parse_req_txt(req_file)
            if block:
                sections.append(block)

        def _parse_pyproject(pyproject_file: Path) -> str | None:
            content = pyproject_file.read_text(encoding="utf-8", errors="replace")
            if len(content) > MAX_REQUIREMENTS_CHARS:
                content = content[:MAX_REQUIREMENTS_CHARS] + "\n...[truncated]"

            dep_lines = []
            in_deps = False
            section_pat = re.compile(r"^\[(.+)\]")
            dep_sections = {
                "project.dependencies",
                "tool.poetry.dependencies",
                "tool.poetry.dev-dependencies",
                "project.optional-dependencies",
            }

            in_project = False
            for line in content.splitlines():
                m = section_pat.match(line.strip())
                if m:
                    section = m.group(1)
                    section_lower = section.lower()
                    if section_lower in dep_sections:
                        in_deps = True
                        dep_lines.append(f"\n  [{section}]")
                    elif section_lower == "project":
                        in_project = True
                        in_deps = False
                        dep_lines.append(f"\n  [project]")
                    else:
                        in_deps = False
                        in_project = False
                    continue
                if in_deps and line.strip():
                    dep_lines.append(f"  {line.strip()}")
                elif in_project and line.strip():
                    stripped = line.strip()
                    if stripped.startswith("dependencies") or stripped.startswith("optional-dependencies"):
                        in_deps = True
                        dep_lines.append(f"  {stripped}")
                    elif in_deps and (stripped.startswith('"') or stripped.startswith("'")):
                        dep_lines.append(f"  {stripped}")

            if not dep_lines:
                return None

            try:
                rel = pyproject_file.relative_to(root)
            except ValueError:
                rel = pyproject_file

            block = f"pyproject.toml ({rel}):\n"
            block += "".join(dep_lines)
            return block

        for pyproject_file in pyproject_files:
            block = _parse_pyproject(pyproject_file)
            if block:
                sections.append(block)

        def _parse_pipfile(pipfile: Path) -> str | None:
            try:
                import tomllib
            except ImportError:
                return None
            try:
                data = tomllib.loads(pipfile.read_text(encoding="utf-8"))
            except Exception:
                return None

            dep_lines = []
            for section, key in [("packages", "default"), ("dev-packages", "develop")]:
                if section in data:
                    deps = data[section]
                    if deps:
                        dep_lines.append(f"\n  [{key}]")
                        for pkg, spec in sorted(deps.items()):
                            if isinstance(spec, str):
                                dep_lines.append(f"  {pkg}{spec}")
                            elif isinstance(spec, dict) and "version" in spec:
                                dep_lines.append(f"  {pkg}{spec['version']}")
                            else:
                                dep_lines.append(f"  {pkg}")

            if not dep_lines:
                return None

            try:
                rel = pipfile.relative_to(root)
            except ValueError:
                rel = pipfile
            return f"Pipfile ({rel}):\n" + "\n".join(dep_lines)

        for pipfile in pipfile_files:
            block = _parse_pipfile(pipfile)
            if block:
                sections.append(block)

        def _parse_setup_py(setup_file: Path) -> str | None:
            content = setup_file.read_text(encoding="utf-8", errors="replace")
            dep_lines = []

            install_requires = re.search(r"install_requires\s*=\s*\[([^\]]+)\]", content, re.DOTALL)
            if install_requires:
                dep_lines.append("\n  [install_requires]")
                for dep in install_requires.group(1).split(","):
                    dep = dep.strip().strip("'\"").strip()
                    if dep:
                        dep_lines.append(f"  {dep}")

            extras_require = re.search(r"extras_require\s*=\s*\{([^}]+)\}", content, re.DOTALL)
            if extras_require:
                in_extras = False
                for line in extras_require.group(1).split(","):
                    if "=" in line:
                        if not in_extras:
                            dep_lines.append("\n  [extras_require]")
                            in_extras = True
                        dep_lines.append(f"  {line.strip()}")

            if not dep_lines:
                return None

            try:
                rel = setup_file.relative_to(root)
            except ValueError:
                rel = setup_file
            return f"setup.py ({rel}):\n" + "\n".join(dep_lines)

        for setup_file in setup_files:
            block = _parse_setup_py(setup_file)
            if block:
                sections.append(block)

        def _parse_setup_cfg(setup_cfg_file: Path) -> str | None:
            content = setup_cfg_file.read_text(encoding="utf-8", errors="replace")
            dep_lines = []
            in_section = False

            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    section = stripped[1:-1].lower()
                    in_section = section in ("options", "options.extras_require")
                    if in_section:
                        dep_lines.append(f"\n  [{stripped}]")
                    continue
                if in_section and "=" in stripped and not stripped.startswith("#"):
                    if stripped.split("=")[0].strip() in ("install_requires", "packages"):
                        dep_lines.append(f"  {stripped}")

            if not dep_lines:
                return None

            try:
                rel = setup_cfg_file.relative_to(root)
            except ValueError:
                rel = setup_cfg_file
            return f"setup.cfg ({rel}):\n" + "\n".join(dep_lines)

        for setup_cfg in setup_cfg_files:
            block = _parse_setup_cfg(setup_cfg)
            if block:
                sections.append(block)

        # ── Parse package.json (JS/TS projects) ──────────────────────────────
        package_json_files = sorted(p for p in root.rglob("package.json")
                                   if not any(part in _skip for part in p.parts))

        def _parse_package_json(pkg_file: Path) -> str | None:
            try:
                data = json.loads(pkg_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                return None

            dep_lines = []
            rel_path = pkg_file.relative_to(root) if pkg_file.is_relative_to(root) else pkg_file

            for section_key, label in [("dependencies", "dependencies"), ("devDependencies", "devDependencies (dev)")]:
                if section_key in data and data[section_key]:
                    dep_lines.append(f"\n  [{label}]")
                    for pkg, version in sorted(data[section_key].items()):
                        dep_lines.append(f"  {pkg} {version}")

            if not dep_lines:
                return None

            return f"package.json ({rel_path}):\n" + "\n".join(dep_lines)

        for pkg_file in package_json_files:
            block = _parse_package_json(pkg_file)
            if block:
                sections.append(block)

        # ── Nothing found ─────────────────────────────────────────────────────
        if not sections:
            return (
                f"[read_requirements: No requirements.txt, pyproject.toml, Pipfile, setup.py, setup.cfg, or package.json found in {root}. "
                f"This project may not have a standard dependency file.]"
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
        root = _resolve_path(root_path) if root_path else _workspace_root()

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
