"""
Gemma Swarm — Code Execution Tools
=====================================
Tools for running Python code and tests inside the workspace sandbox.
install_package requires human confirmation.

All execution is sandboxed to the workspace/tests directory.
stdout and stderr are captured and returned.
"""

import subprocess
import logging
import sys
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from tools.file_tools import _workspace_path, _validate_path

logger = logging.getLogger(__name__)

# Execution limits
EXECUTION_TIMEOUT = 30   # seconds
MAX_OUTPUT_CHARS  = 3000 # truncate long outputs


# ── Schemas ────────────────────────────────────────────────────────────────────

class RunPythonInput(BaseModel):
    path: str = Field(
        description="Path to the Python file to run, relative to workspace root"
    )


class RunTestsInput(BaseModel):
    path: str = Field(
        description="Path to the test file or directory to run with pytest, relative to workspace root"
    )


class InstallPackageInput(BaseModel):
    package: str = Field(
        description="Package name to install via pip (e.g. 'requests', 'numpy==1.24.0')"
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run_subprocess(cmd: list, cwd: str) -> dict:
    """
    Run a subprocess command and return results.
    Returns dict with stdout, stderr, exit_code, success.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=EXECUTION_TIMEOUT,
            cwd=cwd,
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        # Truncate long outputs
        if len(stdout) > MAX_OUTPUT_CHARS:
            stdout = stdout[:MAX_OUTPUT_CHARS] + "\n... [output truncated]"
        if len(stderr) > MAX_OUTPUT_CHARS:
            stderr = stderr[:MAX_OUTPUT_CHARS] + "\n... [error truncated]"

        return {
            "stdout":    stdout,
            "stderr":    stderr,
            "exit_code": result.returncode,
            "success":   result.returncode == 0,
        }

    except subprocess.TimeoutExpired:
        return {
            "stdout":    "",
            "stderr":    f"Execution timed out after {EXECUTION_TIMEOUT} seconds.",
            "exit_code": -1,
            "success":   False,
        }
    except Exception as e:
        return {
            "stdout":    "",
            "stderr":    f"Execution error: {e}",
            "exit_code": -1,
            "success":   False,
        }


def _format_result(result: dict) -> str:
    """Format execution result as readable string."""
    status = "✅ Success" if result["success"] else "❌ Failed"
    parts  = [f"Exit code: {result['exit_code']} — {status}"]

    if result["stdout"]:
        parts.append(f"\nOutput:\n{result['stdout']}")

    if result["stderr"]:
        parts.append(f"\nErrors:\n{result['stderr']}")

    return "\n".join(parts)


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool(args_schema=RunPythonInput)
def run_python(path: str) -> str:
    """
    Run a Python file inside the workspace and return the output.
    Use this to test code written by the coder agent.
    Returns stdout, stderr, and exit code.
    """
    valid, abs_path = _validate_path(path)
    if not valid:
        return f"Error: {abs_path}"

    target = Path(abs_path)

    if not target.exists():
        return f"Error: File not found: {path}"

    if not target.suffix == ".py":
        return f"Error: {path} is not a Python file."

    logger.info(f"[code_executor] Running: {path}")

    result = _run_subprocess(
        cmd=[sys.executable, str(target)],
        cwd=str(target.parent),
    )

    logger.info(f"[code_executor] Exit code: {result['exit_code']}")
    return _format_result(result)


@tool(args_schema=RunTestsInput)
def run_tests(path: str) -> str:
    """
    Run pytest on a test file or directory inside the workspace.
    Returns test results including which tests passed and failed.
    Use this after writing test files to verify code correctness.
    """
    valid, abs_path = _validate_path(path)
    if not valid:
        return f"Error: {abs_path}"

    target = Path(abs_path)

    if not target.exists():
        return f"Error: Path not found: {path}"

    logger.info(f"[code_executor] Running tests: {path}")

    result = _run_subprocess(
        cmd=[sys.executable, "-m", "pytest", str(target), "-v", "--tb=short"],
        cwd=str(Path(_workspace_path)),
    )

    logger.info(f"[code_executor] Tests exit code: {result['exit_code']}")
    return _format_result(result)


@tool(args_schema=InstallPackageInput)
def install_package(package: str) -> str:
    """
    Request installation of a Python package via pip.
    This requires human confirmation before executing.
    The package will NOT be installed immediately.
    """
    # Basic validation — no shell injection
    dangerous = [";", "&", "|", ">", "<", "`", "$", "(", ")", "\n"]
    for char in dangerous:
        if char in package:
            return f"Error: Invalid package name — contains forbidden character: '{char}'"

    # Return confirmation marker
    return f"REQUIRES_CONFIRMATION:install_package:{package}"


# ── Tool Registry ──────────────────────────────────────────────────────────────

CODE_TOOLS = [
    run_python,
    run_tests,
    install_package,
]
