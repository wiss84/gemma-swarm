"""
Gemma Swarm — Coding Agent: Layer 4 Validation Tools
======================================================
Tools the coding agent uses to verify its own work after writing code.
The agent should ALWAYS run these after writing or editing any file.
Never trust unvalidated output — if these tools report errors, fix them.

Tools:
    run_tests(test_path, working_dir)         — pytest / unittest runner
    run_linter(file_path, working_dir)        — ruff (primary) / flake8 (fallback)
    check_imports(file_path, working_dir)     — verify all imports resolve
    run_type_checker(file_path, working_dir)  — mypy (skips gracefully if not installed)

Environment safety:
    All commands are run via subprocess with the conda env redirection logic
    from coding_tools.py — if CONDA_DEFAULT_ENV == gemma_swarm, python/pytest
    commands are prefixed with `conda run -n gemma_test`.

Workflow the agent must follow:
    1. Write the file
    2. check_imports(file)       → verify imports work before anything else
    3. run_linter(file)          → catch style/syntax issues
    4. run_tests(test_file)      → confirm behaviour is correct
    5. run_type_checker(file)    → optional but useful for large modules
    If any step fails → fix the issue → repeat from that step
"""

import re
import os
import logging
import platform
import subprocess
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from agents_utils.config import PROJECT_ROOT, BLOCKED_PATTERNS

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT   = 60     # seconds — tests can take a while
MAX_TIMEOUT       = 300    # 5 minutes hard cap
MAX_OUTPUT_CHARS  = 20_000 # cap linter/test output to avoid flooding context
PROD_ENV_NAME     = "gemma_swarm"
TEST_ENV_NAME     = "gemma_test"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _active_conda_env() -> str:
    return os.environ.get("CONDA_DEFAULT_ENV", "")


def _resolve_path(path: str) -> Path:
    p = Path(path)
    return p.resolve() if p.is_absolute() else (PROJECT_ROOT / path).resolve()


def _run_command(cmd: list[str], cwd: Path, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    """
    Run a command as a list (never shell=True) and return (returncode, stdout, stderr).
    Automatically redirects python/pytest commands to gemma_test if in gemma_swarm env.
    """
    # Redirect to test env if in production
    if _active_conda_env() == PROD_ENV_NAME:
        if cmd[0] in ("python", "python3", "pytest"):
            cmd = ["conda", "run", "-n", TEST_ENV_NAME] + cmd
            logger.info(f"[validation] Redirecting to {TEST_ENV_NAME}: {cmd[:6]}")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError as e:
        return -1, "", f"Command not found: {e}"
    except Exception as e:
        return -1, "", str(e)


def _truncate(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half]
        + f"\n\n... [output truncated — {len(text) - max_chars} chars omitted] ...\n\n"
        + text[-half:]
    )


def _tool_available(tool_name: str) -> bool:
    """Check if a CLI tool is available in PATH."""
    try:
        subprocess.run(
            [tool_name, "--version"],
            capture_output=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _extract_imports(file_path: Path) -> list[str]:
    """
    Parse a Python file and extract all top-level import statements.
    Returns a list of module names to try importing.
    """
    modules = []
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        for line in source.splitlines():
            stripped = line.strip()
            # import X, import X as Y
            m = re.match(r"^import\s+([\w.]+)", stripped)
            if m:
                # Use only the top-level package name
                modules.append(m.group(1).split(".")[0])
                continue
            # from X import Y
            m = re.match(r"^from\s+([\w.]+)\s+import", stripped)
            if m:
                modules.append(m.group(1).split(".")[0])
    except Exception:
        pass
    return list(dict.fromkeys(modules))  # deduplicate while preserving order


# ── Tool 1: run_tests ─────────────────────────────────────────────────────────

class RunTestsInput(BaseModel):
    test_path:   str = Field(description="Path to a test file or directory to run. Absolute or project-relative.")
    working_dir: str = Field(default="", description="Working directory for the test run. Defaults to project root.")
    timeout:     int = Field(default=DEFAULT_TIMEOUT, description=f"Timeout in seconds. Max {MAX_TIMEOUT}.")


@tool(args_schema=RunTestsInput)
def run_tests(test_path: str, working_dir: str = "", timeout: int = DEFAULT_TIMEOUT) -> str:
    """
    Run a test file or directory using pytest (preferred) or unittest (fallback).
    Returns the full output including pass/fail counts and any error tracebacks.
    Always run this after writing or modifying code to confirm it works.
    Returns an error string starting with '[' on failure to even launch.
    """
    timeout = min(max(5, timeout), MAX_TIMEOUT)

    try:
        resolved = _resolve_path(test_path)
        if not resolved.exists():
            return f"[run_tests error: Test path not found: {resolved}]"

        cwd = _resolve_path(working_dir) if working_dir else PROJECT_ROOT
        if not cwd.exists():
            return f"[run_tests error: Working directory not found: {cwd}]"

        # Prefer pytest; fall back to unittest
        if _tool_available("pytest"):
            cmd = ["pytest", str(resolved), "-v", "--tb=short", "--no-header"]
            runner = "pytest"
        else:
            cmd = ["python", "-m", "unittest", str(resolved), "-v"]
            runner = "unittest"

        logger.info(f"[validation] Running {runner}: {resolved}")
        rc, stdout, stderr = _run_command(cmd, cwd, timeout)

        output = ""
        if stdout:
            output += stdout
        if stderr:
            output += ("\n\n" if output else "") + stderr

        output = _truncate(output)

        # Build a clean summary header
        status = "PASSED" if rc == 0 else "FAILED"
        header = f"Test run ({runner}): {status}\nPath: {resolved}\nExit code: {rc}\n" + "─" * 60 + "\n\n"

        return header + (output if output else "(no output)")

    except Exception as e:
        return f"[run_tests error: {e}]"


# ── Tool 2: run_linter ────────────────────────────────────────────────────────

class RunLinterInput(BaseModel):
    file_path:   str = Field(description="Path to the Python file to lint. Absolute or project-relative.")
    working_dir: str = Field(default="", description="Working directory. Defaults to project root.")


@tool(args_schema=RunLinterInput)
def run_linter(file_path: str, working_dir: str = "") -> str:
    """
    Run ruff (primary linter) or flake8 (fallback) on a Python file.
    Auto-detects which linter is available — prefers ruff since it's faster.
    Returns issues with file:line:col:code:message format, or a clean bill
    of health if no issues are found.
    Always lint after writing code before running tests — catch style/syntax
    issues first since they will cause tests to fail anyway.
    Returns an error string starting with '[' on failure to launch.
    """
    try:
        resolved = _resolve_path(file_path)
        if not resolved.exists():
            return f"[run_linter error: File not found: {resolved}]"
        if not resolved.suffix == ".py":
            return f"[run_linter error: Not a Python file: {resolved}]"

        cwd = _resolve_path(working_dir) if working_dir else PROJECT_ROOT

        # Try ruff first, fall back to flake8
        if _tool_available("ruff"):
            cmd    = ["ruff", "check", str(resolved), "--output-format=concise"]
            linter = "ruff"
        elif _tool_available("flake8"):
            cmd    = ["flake8", str(resolved), "--max-line-length=120"]
            linter = "flake8"
        else:
            return (
                "[run_linter: Neither ruff nor flake8 is installed. "
                "Install with: pip install ruff  OR  pip install flake8]"
            )

        logger.info(f"[validation] Running {linter}: {resolved}")
        rc, stdout, stderr = _run_command(cmd, cwd)

        # ruff/flake8: exit 0 = no issues, exit 1 = issues found
        if rc == 0:
            return f"Linter ({linter}): ✓ No issues found in {resolved.name}"

        output = stdout or stderr
        output = _truncate(output)

        # Count issues for the summary
        issue_count = len([l for l in output.splitlines() if l.strip() and not l.startswith("Found")])
        header = f"Linter ({linter}): {issue_count} issue(s) found in {resolved.name}\n" + "─" * 60 + "\n\n"
        return header + output

    except Exception as e:
        return f"[run_linter error: {e}]"


# ── Tool 3: check_imports ─────────────────────────────────────────────────────

class CheckImportsInput(BaseModel):
    file_path:   str = Field(description="Path to the Python file to check imports for. Absolute or project-relative.")
    working_dir: str = Field(default="", description="Working directory. Defaults to project root.")


@tool(args_schema=CheckImportsInput)
def check_imports(file_path: str, working_dir: str = "") -> str:
    """
    Verify that all import statements in a Python file resolve correctly.
    Parses the file to extract all imports, then tries importing each one.
    This catches missing packages and typos in import paths before running tests.
    Call this immediately after writing a new file, before linting or testing.
    Returns a per-import pass/fail report.
    Returns an error string starting with '[' if the file cannot be read.
    """
    try:
        resolved = _resolve_path(file_path)
        if not resolved.exists():
            return f"[check_imports error: File not found: {resolved}]"
        if resolved.suffix != ".py":
            return f"[check_imports error: Not a Python file: {resolved}]"

        cwd = _resolve_path(working_dir) if working_dir else PROJECT_ROOT

        modules = _extract_imports(resolved)
        if not modules:
            return f"check_imports: No import statements found in {resolved.name}"

        results  = []
        failures = []

        for module in modules:
            rc, stdout, stderr = _run_command(
                ["python", "-c", f"import {module}"],
                cwd,
                timeout=10,
            )
            if rc == 0:
                results.append(f"  ✓ import {module}")
            else:
                # Extract the most relevant error line
                err_lines = (stderr or stdout).splitlines()
                err_msg   = next(
                    (l for l in reversed(err_lines) if l.strip()),
                    "unknown error"
                )
                results.append(f"  ✗ import {module}  →  {err_msg}")
                failures.append(module)

        passed = len(modules) - len(failures)
        status = "✓ All imports OK" if not failures else f"✗ {len(failures)} import(s) failed"
        header = (
            f"check_imports: {status}\n"
            f"File: {resolved.name}  ({passed}/{len(modules)} imports OK)\n"
            + "─" * 60 + "\n\n"
        )
        return header + "\n".join(results)

    except Exception as e:
        return f"[check_imports error: {e}]"


# ── Tool 4: run_type_checker ──────────────────────────────────────────────────

class RunTypeCheckerInput(BaseModel):
    file_path:   str = Field(description="Path to the Python file to type-check. Absolute or project-relative.")
    working_dir: str = Field(default="", description="Working directory. Defaults to project root.")


@tool(args_schema=RunTypeCheckerInput)
def run_type_checker(file_path: str, working_dir: str = "") -> str:
    """
    Run mypy on a Python file to check for type errors.
    Uses --ignore-missing-imports so it doesn't fail on untyped third-party packages.
    If mypy is not installed, returns a graceful skip message rather than an error.
    Type checking is optional but recommended for new modules — run it last after
    check_imports, run_linter, and run_tests all pass.
    Returns an error string starting with '[' on failure to launch.
    """
    try:
        resolved = _resolve_path(file_path)
        if not resolved.exists():
            return f"[run_type_checker error: File not found: {resolved}]"
        if resolved.suffix != ".py":
            return f"[run_type_checker error: Not a Python file: {resolved}]"

        if not _tool_available("mypy"):
            return (
                f"[run_type_checker: mypy is not installed — skipping type check for {resolved.name}. "
                f"Install with: pip install mypy]"
            )

        cwd = _resolve_path(working_dir) if working_dir else PROJECT_ROOT

        cmd = ["mypy", str(resolved), "--ignore-missing-imports", "--no-error-summary"]
        logger.info(f"[validation] Running mypy: {resolved}")
        rc, stdout, stderr = _run_command(cmd, cwd)

        output = stdout or stderr
        output = _truncate(output)

        if rc == 0:
            return f"Type checker (mypy): ✓ No type errors found in {resolved.name}"

        # Count error lines
        error_lines = [l for l in output.splitlines() if ": error:" in l]
        note_lines  = [l for l in output.splitlines() if ": note:" in l]
        header = (
            f"Type checker (mypy): {len(error_lines)} error(s), {len(note_lines)} note(s) in {resolved.name}\n"
            + "─" * 60 + "\n\n"
        )
        return header + output

    except Exception as e:
        return f"[run_type_checker error: {e}]"
