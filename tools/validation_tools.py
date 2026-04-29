"""
Gemma Swarm — Coding Agent: Layer 4 Validation Tools (Python)
==============================================================
Python-specific validation tools used internally by validate_files_universal.
Not registered as agent tools directly — the agent uses validate_files (below)
which routes to the correct language validator via Magika detection.

For JS/TS and other language validation, see: validation_tools_universal.py

Environment safety:
    All commands are run via subprocess with absolute Python exe redirection.
    If CONDA_DEFAULT_ENV == gemma_swarm, python/pytest/pip commands are
    substituted with the absolute path to the gemma_test Python executable.
"""

import re
import logging
import os
import subprocess
import threading
from queue import Queue, Empty
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool  # kept: validate_files is still a @tool
from agents_utils.get_test_env import get_gemma_test_python_exe
from tools.coding_tools import _workspace_root

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT   = 60     # seconds — tests can take a while
MAX_TIMEOUT       = 300    # 5 minutes hard cap
MAX_OUTPUT_CHARS  = 20_000 # cap linter/test output to avoid flooding context
PROD_ENV_NAME     = "gemma_swarm"
TEST_ENV_NAME     = "gemma_test"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _resolve_path(path: str) -> Path:
    p = Path(path)
    return p.resolve() if p.is_absolute() else (_workspace_root() / path).resolve()


def _run_command(cmd: list[str], cwd: Path, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    """
    Run a command as a list (never shell=True) and return (returncode, stdout, stderr).
    ALWAYS redirects python/pytest/pip/ruff/mypy commands to the gemma_test environment
    by substituting the absolute Python executable path.
    Does NOT rely on CONDA_DEFAULT_ENV — that variable is unreliable at tool runtime.
    Safe from deadlocks — uses threaded output draining.
    """
    py_exe = get_gemma_test_python_exe()
    if cmd[0] in ("python", "python3"):
        cmd = [py_exe] + cmd[1:]
        logger.info(f"[validation] Using {TEST_ENV_NAME} python: {py_exe}")
    elif cmd[0] == "pytest":
        cmd = [py_exe, "-m", "pytest"] + cmd[1:]
        logger.info(f"[validation] Using {TEST_ENV_NAME} pytest via: {py_exe} -m pytest")
    elif cmd[0] in ("pip", "pip3"):
        cmd = [py_exe, "-m", "pip"] + cmd[1:]
        logger.info(f"[validation] Using {TEST_ENV_NAME} pip via: {py_exe} -m pip")
    elif cmd[0] == "ruff":
        cmd = [py_exe, "-m", "ruff"] + cmd[1:]
        logger.info(f"[validation] Using {TEST_ENV_NAME} ruff via: {py_exe} -m ruff")
    elif cmd[0] == "mypy":
        cmd = [py_exe, "-m", "mypy"] + cmd[1:]
        logger.info(f"[validation] Using {TEST_ENV_NAME} mypy via: {py_exe} -m mypy")
    elif cmd[0] == "flake8":
        cmd = [py_exe, "-m", "flake8"] + cmd[1:]
        logger.info(f"[validation] Using {TEST_ENV_NAME} flake8 via: {py_exe} -m flake8")

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
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return -1, "", f"Command timed out after {timeout}s"

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
    """Check if a CLI tool is available in PATH. Safe from deadlocks."""
    try:
        proc = subprocess.Popen(
            [tool_name, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ},
        )

        # For a tiny output like --version, we can just wait and drain after
        # Using same pattern ensures no deadlock even if tool misbehaves
        stdout_queue = Queue()
        stderr_queue = Queue()

        def _drain_pipe(pipe, queue):
            for line in iter(pipe.readline, b''):
                queue.put(line)
            pipe.close()

        threading.Thread(target=_drain_pipe, args=(proc.stdout, stdout_queue), daemon=True).start()
        threading.Thread(target=_drain_pipe, args=(proc.stderr, stderr_queue), daemon=True).start()

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return False

        # Drain any output (not needed for boolean check, but prevents resource leaks)
        while True:
            try:
                stdout_queue.get_nowait()
            except Empty:
                break
        while True:
            try:
                stderr_queue.get_nowait()
            except Empty:
                break

        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    except Exception:
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


# ── validate_files (Python) ──────────────────────────────────────────────────

class ValidateFilesInput(BaseModel):
    file_paths:  list[str] = Field(
        description=(
            "List of Python file paths to check_imports + run_linter on (absolute or project-relative). "
            "Up to 10 files. Pass all files you just wrote or edited in one call."
        )
    )
    test_path:   str = Field(
        default="",
        description=(
            "Optional: path to a test file or directory to run after linting. "
            "If provided, run_tests is called once all files pass linting. "
            "Leave empty to skip test execution."
        )
    )
    working_dir: str = Field(default="", description="Working directory. Defaults to project root.")


@tool(args_schema=ValidateFilesInput)
def validate_files(file_paths: list[str], test_path: str = "", working_dir: str = "") -> str:
    """
    Run check_imports + run_linter on multiple files in one call, then optionally
    run tests and type check, all in a single tool invocation.
    Use this after write_files or edit_files to validate everything at once 
    Workflow: check_imports on each file → run_linter on each file → run_tests (if test_path given) → run type check on each file.
    Stops at the first import failure so you can fix it before linting.
    Returns a structured report with per-file results and an overall pass/fail.
    Up to 10 file_paths per call.
    """
    if not file_paths:
        return "[validate_files error: file_paths list is empty]"
    if len(file_paths) > 10:
        return "[validate_files error: too many files — max 10 per call]"

    cwd = _resolve_path(working_dir) if working_dir else _workspace_root()
    import_failures  = []
    lint_failures    = []
    report_lines     = []
    all_imports_ok   = True

    # ── Phase 1: check_imports on all files ────────────────────────────────
    report_lines.append("=== Phase 1: Import Check ===")
    for file_path in file_paths:
        try:
            resolved = _resolve_path(file_path)
            if not resolved.exists():
                report_lines.append(f"  ✗ {file_path}: file not found")
                import_failures.append(file_path)
                all_imports_ok = False
                continue

            modules = _extract_imports(resolved)
            if not modules:
                report_lines.append(f"  ✓ {file_path}: no imports")
                continue

            file_ok = True
            for module in modules:
                # Use forward slashes to avoid Windows backslash unicode-escape errors.
                safe_cwd = str(cwd).replace("\\", "/")
                inject = f"import sys; sys.path.insert(0, r'{safe_cwd}'); "
                rc, _, stderr = _run_command(
                    ["python", "-c", inject + f"import {module}"],
                    cwd,
                    timeout=10,
                )
                if rc != 0:
                    err_lines = stderr.splitlines()
                    err_msg = next((l for l in reversed(err_lines) if l.strip()), "unknown error")
                    report_lines.append(f"  ✗ {file_path}: import {module}  →  {err_msg}")
                    import_failures.append(file_path)
                    file_ok = False
                    all_imports_ok = False
                    break  # one failure per file is enough signal

            if file_ok:
                report_lines.append(f"  ✓ {file_path}: all {len(modules)} import(s) OK")

        except Exception as e:
            report_lines.append(f"  ✗ {file_path}: error — {e}")
            import_failures.append(file_path)
            all_imports_ok = False

    # ── Phase 2: run_linter on all files (even if some imports failed — linter is safe) ─
    report_lines.append("\n=== Phase 2: Linter ===")
    for file_path in file_paths:
        try:
            resolved = _resolve_path(file_path)
            if not resolved.exists():
                report_lines.append(f"  ✗ {file_path}: file not found")
                lint_failures.append(file_path)
                continue
            if resolved.suffix != ".py":
                report_lines.append(f"  — {file_path}: skipped (not a .py file)")
                continue

            if _tool_available("ruff"):
                cmd    = ["ruff", "check", str(resolved), "--output-format=concise"]
                linter = "ruff"
            elif _tool_available("flake8"):
                cmd    = ["flake8", str(resolved), "--max-line-length=120"]
                linter = "flake8"
            else:
                report_lines.append(f"  — {file_path}: no linter installed (ruff/flake8)")
                continue

            rc, stdout, stderr = _run_command(cmd, cwd)
            if rc == 0:
                report_lines.append(f"  ✓ {file_path}: no lint issues ({linter})")
            else:
                output = _truncate(stdout or stderr, 2000)
                report_lines.append(f"  ✗ {file_path}: lint issues ({linter})\n{output}")
                lint_failures.append(file_path)

        except Exception as e:
            report_lines.append(f"  ✗ {file_path}: lint error — {e}")
            lint_failures.append(file_path)

    # ── Phase 3: run_tests (optional, only if all imports and lint passed) ───────
    test_output = ""
    if test_path:
        report_lines.append("\n=== Phase 3: Tests ===")
        if import_failures or lint_failures:
            skipped_reason = ", ".join(
                ([f"{len(import_failures)} import failure(s)"] if import_failures else []) +
                ([f"{len(lint_failures)} lint failure(s)"] if lint_failures else [])
            )
            report_lines.append(f"  ⏭ Skipped: fix {skipped_reason} first")
        else:
            try:
                resolved_test = _resolve_path(test_path)
                if not resolved_test.exists():
                    report_lines.append(f"  ✗ test path not found: {resolved_test}")
                else:
                    py_exe = get_gemma_test_python_exe()
                    rc_check, _, _ = _run_command([py_exe, "-m", "pytest", "--version"], _workspace_root(), timeout=5)
                    if rc_check == 0:
                        test_cmd = ["pytest", str(resolved_test), "-v", "--tb=short", "--no-header"]
                        runner   = "pytest"
                    else:
                        test_cmd = ["python", "-m", "unittest", str(resolved_test), "-v"]
                        runner   = "unittest"

                    rc_test, stdout_t, stderr_t = _run_command(test_cmd, cwd, timeout=DEFAULT_TIMEOUT)
                    raw = _truncate((stdout_t + "\n\n" + stderr_t).strip())
                    status = "PASSED" if rc_test == 0 else "FAILED"
                    report_lines.append(f"  {status} ({runner}, exit {rc_test})\n{raw}")
            except Exception as e:
                report_lines.append(f"  ✗ test run error: {e}")

    # ── Phase 4: run_type_checker on all files ───────────────────────────────────────
    type_errors = []
    if not import_failures and not lint_failures:
        report_lines.append("\n=== Phase 4: Type Check ===")
        for file_path in file_paths:
            try:
                resolved = _resolve_path(file_path)
                if not resolved.exists():
                    report_lines.append(f"  ✗ {file_path}: file not found")
                    type_errors.append(file_path)
                    continue
                if resolved.suffix != ".py":
                    report_lines.append(f"  — {file_path}: skipped (not a .py file)")
                    continue

                if not _tool_available("mypy"):
                    report_lines.append(f"  — {file_path}: mypy not installed")
                    continue

                cmd = ["mypy", str(resolved), "--ignore-missing-imports", "--no-error-summary"]
                rc, stdout, stderr = _run_command(cmd, cwd)

                output = stdout or stderr
                if rc == 0:
                    report_lines.append(f"  ✓ {file_path}: no type errors (mypy)")
                else:
                    output_trunc = _truncate(output, 2000)
                    error_count = len([l for l in output.splitlines() if ": error:" in l])
                    report_lines.append(f"  ✗ {file_path}: {error_count} type error(s)\n{output_trunc}")
                    type_errors.append(file_path)

            except Exception as e:
                report_lines.append(f"  ✗ {file_path}: type check error — {e}")
                type_errors.append(file_path)

    # ── Overall summary ───────────────────────────────────────────────────────────
    total_issues = len(import_failures) + len(lint_failures) + len(type_errors)
    overall = "✓ All checks passed" if total_issues == 0 else f"✗ {total_issues} issue(s) found"
    header = (
        f"validate_files: {overall}\n"
        f"Files checked: {len(file_paths)}  |  "
        f"Import failures: {len(import_failures)}  |  "
        f"Lint failures: {len(lint_failures)}  |  "
        f"Type errors: {len(type_errors)}\n"
        + "─" * 60 + "\n\n"
    )
    return header + "\n".join(report_lines)
