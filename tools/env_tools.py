"""
Gemma Swarm — Coding Agent: Layer 6 Environment Tools
======================================================
Tools for introspecting the Python environment the coding agent is running in.

Tools:
    get_python_info()                   — Python version, executable, venv, pip, platform
    get_env_variables(keys)             — Inspect specific env vars by name (whitelist-gated)
    install_package(package, version,   — pip install with mandatory Slack human approval
                    thread_ts, channel)   before executing

Design rules:
    - get_env_variables NEVER dumps the full environment. Only returns keys that
      are in SAFE_ENV_KEYS. All other keys return [BLOCKED — not in whitelist].
    - install_package NEVER runs pip install without human approval via Slack.
      Without Slack context (e.g. unit tests), it refuses to proceed.
    - All installs target the gemma_test conda environment (same env-redirect
      logic as coding_tools.py and validation_tools.py).
"""

import os
import sys
import platform
import logging
import subprocess
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from agents_utils.config import HUMAN_CONFIRMATION_TIMEOUT

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

PROD_ENV_NAME = "gemma_swarm"
TEST_ENV_NAME = "gemma_test"

# Whitelist of env var names the agent is allowed to inspect.
# NEVER add OAuth tokens, passwords, or private keys here.
# Keys here return their actual value (masked if sensitive-looking).
# Keys NOT here return [BLOCKED — not in whitelist].
SAFE_ENV_KEYS = {
    # API keys the agent legitimately needs to know about
    "GOOGLE_API_KEY",
    "JINA_API_KEY",
    # Conda / Python environment info
    "CONDA_DEFAULT_ENV",
    "CONDA_PREFIX",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    # Path info
    "PATH",
    "HOME",
    "USERPROFILE",
    # Project-specific
    "GEMMA_SWARM_ENV",
    "GEMMA_SWARM_WORKSPACE",
    # CI / build context
    "CI",
    "GITHUB_ACTIONS",
    "RUNNER_OS",
}

# Keys that are in SAFE_ENV_KEYS but whose values should be masked
# (show [SET] instead of the actual value).
MASKED_KEYS = {
    "GOOGLE_API_KEY",
    "JINA_API_KEY",
}


# ── Internal helpers ───────────────────────────────────────────────────────────

def _active_conda_env() -> str:
    return os.environ.get("CONDA_DEFAULT_ENV", "")


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command list and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
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


def _pip_version() -> str:
    """Return pip version string, or 'unknown' if not found."""
    rc, stdout, _ = _run([sys.executable, "-m", "pip", "--version"])
    if rc == 0 and stdout:
        # "pip 23.0 from /path/to/pip (python 3.11)" → "23.0"
        parts = stdout.split()
        return parts[1] if len(parts) >= 2 else stdout
    return "unknown"


# ── Tool 1: get_python_info ───────────────────────────────────────────────────

@tool
def get_python_info() -> str:
    """
    Return information about the current Python environment.
    Reports: Python version, executable path, active conda/venv name,
    pip version, OS/platform, and which environment commands will run in.
    Call this at the start of any coding session to understand the environment.
    Returns an error string starting with '[' on failure.
    """
    try:
        active_conda = _active_conda_env()
        active_venv  = os.environ.get("VIRTUAL_ENV", "")

        # Determine the effective execution environment
        if active_conda == PROD_ENV_NAME:
            exec_env = f"{TEST_ENV_NAME} (redirected — active env is {PROD_ENV_NAME})"
        elif active_conda:
            exec_env = active_conda
        elif active_venv:
            exec_env = f"venv: {active_venv}"
        else:
            exec_env = "system Python (no conda env or venv active)"

        pip_ver = _pip_version()

        lines = [
            "Python Environment Info",
            "─" * 50,
            f"Python version  : {sys.version}",
            f"Executable      : {sys.executable}",
            f"Platform        : {platform.system()} {platform.release()} ({platform.machine()})",
            f"pip version     : {pip_ver}",
            "",
            "Environment context:",
            f"  Active conda env : {active_conda or '(none)'}",
            f"  Active venv      : {active_venv or '(none)'}",
            f"  Commands run in  : {exec_env}",
        ]

        if active_conda == PROD_ENV_NAME:
            lines += [
                "",
                f"⚠️  NOTE: Active env is '{PROD_ENV_NAME}' (production). "
                f"All Python/pytest/pip commands are automatically redirected "
                f"to '{TEST_ENV_NAME}' to protect the production environment.",
            ]

        return "\n".join(lines)

    except Exception as e:
        return f"[get_python_info error: {e}]"


# ── Tool 2: get_env_variables ─────────────────────────────────────────────────

class GetEnvVariablesInput(BaseModel):
    keys: list[str] = Field(
        description=(
            "List of environment variable names to inspect, e.g. ['GOOGLE_API_KEY', 'CONDA_DEFAULT_ENV']. "
            "Only keys in the tool's whitelist are readable. "
            "Unknown or sensitive keys return [BLOCKED — not in whitelist]."
        )
    )


@tool(args_schema=GetEnvVariablesInput)
def get_env_variables(keys: list[str]) -> str:
    """
    Inspect the values of specific environment variables by name.
    NEVER returns the full environment — only the keys you request.
    Only a fixed whitelist of safe keys can be read; everything else is blocked.
    Sensitive keys (like API keys) are masked: returns [SET] or [NOT SET]
    rather than the actual value.
    Use this to check whether required env vars are configured before running code.
    Returns an error string starting with '[' on failure.
    """
    if not keys:
        return "[get_env_variables error: No keys provided. Pass a list of env var names.]"

    if not isinstance(keys, list):
        return "[get_env_variables error: 'keys' must be a list of strings.]"

    lines = ["Environment Variable Check", "─" * 50]

    for key in keys:
        key = key.strip()
        if not key:
            continue

        if key not in SAFE_ENV_KEYS:
            lines.append(f"  {key:<35} [BLOCKED — not in whitelist]")
            continue

        value = os.environ.get(key)

        if value is None:
            lines.append(f"  {key:<35} [NOT SET]")
        elif key in MASKED_KEYS:
            lines.append(f"  {key:<35} [SET]")
        else:
            # Truncate very long values (e.g. PATH)
            display = value if len(value) <= 120 else value[:120] + "..."
            lines.append(f"  {key:<35} {display}")

    lines += [
        "",
        f"Note: Only keys in the tool whitelist ({len(SAFE_ENV_KEYS)} keys) can be inspected.",
        "Sensitive keys (API keys) show [SET]/[NOT SET] only — values are never exposed.",
    ]

    return "\n".join(lines)


# ── Tool 3: install_package ───────────────────────────────────────────────────

class InstallPackageInput(BaseModel):
    package_name: str = Field(
        description="Name of the pip package to install, e.g. 'httpx' or 'langchain-core'.",
    )
    version: str = Field(
        default="",
        description=(
            "Optional version specifier, e.g. '0.27.0' or '>=0.25,<0.28'. "
            "If empty, pip installs the latest compatible version."
        ),
    )
    thread_ts: str = Field(
        default="",
        description="Slack thread timestamp for posting the human approval request.",
    )
    channel: str = Field(
        default="",
        description="Slack channel ID for posting the human approval request.",
    )


@tool(args_schema=InstallPackageInput)
def install_package(
    package_name: str,
    version: str = "",
    thread_ts: str = "",
    channel: str = "",
) -> str:
    """
    Install a pip package — but ONLY after receiving explicit human approval via Slack.
    Posts an Approve/Reject confirmation to the Slack thread and waits for a response.
    If no Slack context is provided (e.g. unit tests), this tool refuses to run.
    Installs into the gemma_test conda environment, never into gemma_swarm (production).
    After install, verifies the package is importable and reports the installed version.
    Returns an error string starting with '[' on failure or rejection.
    """
    package_name = package_name.strip()
    if not package_name:
        return "[install_package error: package_name cannot be empty.]"

    # Build the pip install target string
    if version:
        version = version.strip()
        # If version looks like a bare version number (no specifier), add ==
        if version and version[0].isdigit():
            pip_target = f"{package_name}=={version}"
        else:
            pip_target = f"{package_name}{version}"
    else:
        pip_target = package_name

    # ── Require Slack human confirmation ─────────────────────────────────────
    if not thread_ts or not channel:
        return (
            f"[install_package error: Human approval is required before installing '{pip_target}'. "
            f"Provide thread_ts and channel so the agent can post an Approve/Reject message to Slack. "
            f"Do NOT attempt to install without approval.]"
        )

    from nodes.human_gate import (
        register_confirmation,
        resolve_confirmation,
        get_decision,
        clear_confirmation,
        build_confirmation_blocks,
    )

    try:
        from slack_sdk import WebClient
        slack_token = os.environ.get("Bot_User_OAuth_Token", "")
        if not slack_token:
            return "[install_package error: Bot_User_OAuth_Token not set — cannot post confirmation message.]"
        client = WebClient(token=slack_token)
    except ImportError:
        return "[install_package error: slack_sdk not installed — cannot post confirmation message.]"

    # Determine target environment
    active_conda = _active_conda_env()
    if active_conda == PROD_ENV_NAME:
        target_env = TEST_ENV_NAME
        env_note   = f"Will install into `{TEST_ENV_NAME}` (redirected — never touches `{PROD_ENV_NAME}`)."
    else:
        target_env = active_conda or "current environment"
        env_note   = f"Will install into `{target_env}`."

    pending_action = (
        f"📦 *Package Install Request*\n\n"
        f"The coding agent wants to install:\n"
        f"```pip install {pip_target}```\n\n"
        f"{env_note}\n\n"
        f"Approve to proceed, Reject to cancel."
    )

    event = register_confirmation(thread_ts)

    try:
        blocks = build_confirmation_blocks(pending_action, thread_ts)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"📦 The coding agent wants to install `{pip_target}`. Approve?",
            blocks=blocks,
        )
        logger.info(f"[install_package] Posted confirmation for '{pip_target}' to {thread_ts}")
    except Exception as e:
        clear_confirmation(thread_ts)
        return f"[install_package error: Could not post confirmation to Slack: {e}]"

    # Wait for human response
    responded = event.wait(timeout=HUMAN_CONFIRMATION_TIMEOUT)
    decision  = get_decision(thread_ts) if responded else "rejected"

    if not responded:
        logger.warning(f"[install_package] Confirmation timed out for '{pip_target}' — defaulting to rejected.")

    clear_confirmation(thread_ts)

    if not decision or decision.startswith("rejected"):
        reason = decision[len("rejected:"):].strip() if decision and ":" in decision else ""
        msg = f"[install_package: Install of '{pip_target}' rejected by human."
        if reason:
            msg += f" Feedback: {reason}"
        msg += " No packages were installed.]"
        return msg

    # ── Human approved — run pip install ─────────────────────────────────────
    logger.info(f"[install_package] Approved. Installing '{pip_target}' into '{target_env}'.")

    if active_conda == PROD_ENV_NAME:
        cmd = ["conda", "run", "-n", TEST_ENV_NAME, "pip", "install", pip_target]
    else:
        cmd = [sys.executable, "-m", "pip", "install", pip_target]

    rc, stdout, stderr = _run(cmd, timeout=120)

    output = stdout or stderr
    if rc != 0:
        return (
            f"[install_package error: pip install failed for '{pip_target}'.\n"
            f"Exit code: {rc}\n"
            f"Output:\n{output[:3000]}]"
        )

    # Verify it's importable — use the top-level module name
    import_name = package_name.replace("-", "_").split("[")[0]  # handle extras like package[extra]
    verify_cmd  = (
        ["conda", "run", "-n", TEST_ENV_NAME, "python", "-c", f"import {import_name}"]
        if active_conda == PROD_ENV_NAME
        else [sys.executable, "-c", f"import {import_name}"]
    )
    rc_verify, _, stderr_verify = _run(verify_cmd, timeout=15)

    import_status = (
        f"✓ `import {import_name}` verified successfully"
        if rc_verify == 0
        else f"⚠️  `import {import_name}` failed after install: {stderr_verify[:200]}"
    )

    return (
        f"install_package: ✓ Installed '{pip_target}' into '{target_env}'\n"
        f"{import_status}\n\n"
        f"pip output (last 1000 chars):\n{output[-1000:]}"
    )
