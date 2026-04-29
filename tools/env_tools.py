"""
Gemma Swarm — Coding Agent: Layer 6 Environment Tools
======================================================
Tools for introspecting the Python environment the coding agent is running in.

Tools:
    get_python_info()                   — Python version, executable, venv, pip, platform
    get_env_variables()                 — Discover .env / config files in workspace and
                                          return their KEYS ONLY — never values.
    install_package(package, version,   — pip install with mandatory Slack human approval
                    thread_ts, channel)   before executing

Design rules:
    - get_env_variables NEVER returns values. It scans the workspace for .env
      and similar config files and reports only the variable names found inside.
    - install_package NEVER runs pip install without human approval via Slack.
      Without Slack context (e.g. unit tests), it refuses to proceed.
    - All installs target the gemma_test conda environment (same env-redirect
      logic as coding_tools.py and validation_tools.py).
"""
import sys
import os
import re
import platform
import logging
import subprocess
import threading
from queue import Queue, Empty
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from agents_utils.config import HUMAN_CONFIRMATION_TIMEOUT
from agents_utils.get_test_env import get_gemma_test_python_exe

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

PROD_ENV_NAME = "gemma_swarm"
TEST_ENV_NAME = "gemma_test"

# ── Module-level Slack context (set per coding session) ────────────────────────
# Similar to set_coding_workspace_root() in coding_tools.py
# Set by run_coding_session_slack() to provide Slack context for human gates
_SLACK_CONTEXT = {
    "thread_ts": None,
    "channel": None,
    "client": None,
}

def set_coding_slack_context(thread_ts: str, channel: str, client):
    """Set the Slack context for this coding session. Called once at session start."""
    global _SLACK_CONTEXT
    _SLACK_CONTEXT = {
        "thread_ts": thread_ts,
        "channel": channel,
        "client": client,
    }

# Filenames (case-insensitive) that are treated as env/config files.
# Only KEYS are extracted — values are never read or returned.
ENV_FILE_PATTERNS = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".env.example",
    ".env.sample",
    ".envrc",
    "config.env",
    "secrets.env",
}

# Regex that matches KEY=... lines in env files.
# Captures the key name only; the value is intentionally ignored.
_ENV_KEY_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=')


# ── Internal helpers ───────────────────────────────────────────────────────────

def _active_conda_env() -> str:
    return os.environ.get("CONDA_DEFAULT_ENV", "")


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command list and return (returncode, stdout, stderr). Safe from deadlocks."""
    try:
        # Build command string for shell; on Windows shell=True is often needed
        # Use string form to preserve shell behavior; caller passes full command already.
        command = subprocess.list2cmdline(cmd) if isinstance(cmd, list) else str(cmd)

        proc = subprocess.Popen(
            command,
            shell=True,
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
    Return information about the Python environment in use by the coding agent.
    Always queries the gemma_test environment when running in production.
    Reports: Python version, pip version, platform, and environment context.
    Call this at the start of any coding session to understand the environment.
    Returns an error string starting with '[' on failure.
    """
    try:
        active_conda = _active_conda_env()
        active_venv  = os.environ.get("VIRTUAL_ENV", "")

        # Determine which Python to query
        if active_conda == PROD_ENV_NAME:
            # Query gemma_test environment
            py_exe = get_gemma_test_python_exe()
            rc_ver, py_version, _ = _run([py_exe, "--version"], timeout=10)
            py_version = py_version.strip() if rc_ver == 0 else "unknown"
            
            rc_pip, pip_ver, _ = _run([py_exe, "-m", "pip", "--version"], timeout=10)
            if rc_pip == 0 and pip_ver:
                parts = pip_ver.split()
                pip_ver = parts[1] if len(parts) >= 2 else pip_ver.strip()
            else:
                pip_ver = "unknown"
        else:
            # Query current environment
            rc_ver, py_version, _ = _run([sys.executable, "--version"], timeout=10)
            py_version = py_version.strip() if rc_ver == 0 else "unknown"
            pip_ver = _pip_version()

        lines = [
            "Python Environment Info",
            "─" * 50,
            f"Python version: {py_version}",
            f"pip version:    {pip_ver}",
            f"Platform:       {platform.system()} {platform.release()} ({platform.machine()})",
        ]

        if active_conda == PROD_ENV_NAME:
            lines += [
                "",
                f"✓ Commands execute in '{TEST_ENV_NAME}' (production '{PROD_ENV_NAME}' protected)",
            ]
        elif active_conda:
            lines += [f"Active environment: {active_conda}"]
        elif active_venv:
            lines += [f"Active venv: {active_venv}"]

        return "\n".join(lines)

    except Exception as e:
        return f"[get_python_info error: {e}]"


# ── Tool 2: get_env_variables ─────────────────────────────────────────────────

def _find_env_files(workspace_root: str) -> list[Path]:
    """
    Walk workspace_root (non-recursively for safety) and return paths of files
    whose names match ENV_FILE_PATTERNS. Also checks one level of subdirectories
    for common config locations.
    """
    root = Path(workspace_root)
    found: list[Path] = []

    # Check root directory
    for f in root.iterdir():
        if f.is_file() and f.name.lower() in ENV_FILE_PATTERNS:
            found.append(f)

    # Check one level of subdirectories (e.g. config/, .config/, etc.)
    for subdir in root.iterdir():
        if subdir.is_dir() and not subdir.name.startswith(".git"):
            for f in subdir.iterdir():
                if f.is_file() and f.name.lower() in ENV_FILE_PATTERNS:
                    found.append(f)

    return sorted(found)


def _extract_keys_from_env_file(path: Path) -> list[str]:
    """
    Parse an env file and return only the variable NAMES (keys).
    Values are intentionally ignored — never read into memory.
    Skips blank lines and comment lines (#).
    """
    keys: list[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\n")
                # Skip blanks and comments
                stripped = line.lstrip()
                if not stripped or stripped.startswith("#"):
                    continue
                m = _ENV_KEY_RE.match(line)
                if m:
                    keys.append(m.group(1))
    except OSError:
        pass  # unreadable file — skip silently
    return keys


@tool
def get_env_variables() -> str:
    """
    Discover .env and similar configuration files in the current workspace and
    return only the variable NAMES (keys) found inside them.
    Values are NEVER read, stored, or returned — only key names.
    Use this to understand which environment variables the project expects
    before running code or checking configuration.
    Returns an error string starting with '[' on failure.
    """
    try:
        from tools.coding_tools import _workspace_root
        workspace_root = str(_workspace_root())
        env_files = _find_env_files(workspace_root)

        if not env_files:
            return (
                "[get_env_variables: No .env or config files found in workspace.\n"
                f"Searched in: {workspace_root}\n"
                f"Recognised filenames: {', '.join(sorted(ENV_FILE_PATTERNS))}]"
            )

        lines = [
            "Environment Variable Keys (from workspace config files)",
            "─" * 60,
            f"Workspace: {workspace_root}",
            "",
        ]

        total_keys = 0
        for env_file in env_files:
            rel_path = env_file.relative_to(workspace_root)
            keys = _extract_keys_from_env_file(env_file)
            lines.append(f"📄 {rel_path}  ({len(keys)} keys)")
            if keys:
                for key in keys:
                    lines.append(f"   {key}")
            else:
                lines.append("   (no parseable keys found)")
            lines.append("")
            total_keys += len(keys)

        lines += [
            "─" * 60,
            f"Total: {len(env_files)} file(s), {total_keys} key(s) discovered.",
            "Note: Values are NEVER returned — key names only.",
        ]

        return "\n".join(lines)

    except Exception as e:
        return f"[get_env_variables error: {e}]"


# ── Tool 3: install_package ───────────────────────────────────────────────────

class InstallPackageInput(BaseModel):
    package_name: str = Field(
        description="Name of the package to install, e.g. 'httpx' (Python) or 'axios' (JS/TS).",
    )
    version: str = Field(
        default="",
        description=(
            "Optional version specifier. "
            "For pip: '0.27.0' or '>=0.25,<0.28'. "
            "For npm: '1.6.0' or '^1.0.0'. "
            "If empty, installs the latest version."
        ),
    )
    ecosystem: str = Field(
        default="",
        description=(
            "Package ecosystem: 'pypi' for Python packages, 'npm' for JS/TS packages. "
            "If empty, set path= for npm or defaults to 'pypi'."
        ),
    )
    path: str = Field(
        default="",
        description=(
            "Project directory for npm install (where package.json lives). "
            "Required for npm ecosystem. Ignored for pip installs."
        ),
    )


@tool(args_schema=InstallPackageInput)
def install_package(
    package_name: str,
    version: str = "",
    ecosystem: str = "",
    path: str = "",
) -> str:
    """
    Install a package — but ONLY after receiving explicit human approval via Slack.
    Supports both Python (pip) and JavaScript/TypeScript (npm) packages.
    For Python: installs via pip into gemma_test conda env (or current env).
    For JS/TS: installs via npm in the specified project directory.
    The `path` argument is required for npm installs (where package.json lives).
    Returns an error string starting with '[' on failure or rejection.
    """
    package_name = package_name.strip()
    if not package_name:
        return "[install_package error: package_name cannot be empty.]"

    # Determine ecosystem
    ecosystem = ecosystem.lower().strip()
    if not ecosystem:
        # Default to pip if no path provided, otherwise npm
        ecosystem = "npm" if path else "pypi"

    if ecosystem == "pypi":
        return _install_pip_package(package_name, version)
    elif ecosystem == "npm":
        if not path:
            return "[install_package error: 'path' argument is required for npm installs (project directory with package.json).]"
        return _install_npm_package(package_name, version, path)
    else:
        return f"[install_package error: Invalid ecosystem '{ecosystem}'. Use 'pypi' or 'npm']"


def _install_pip_package(package_name: str, version: str) -> str:
    """Install a Python package via pip with human approval."""
    # Build the pip install target string
    if version:
        version = version.strip()
        if version and version[0].isdigit():
            pip_target = f"{package_name}=={version}"
        else:
            pip_target = f"{package_name}{version}"
    else:
        pip_target = package_name

    # ── Require Slack human confirmation ──────────────────────────────
    thread_ts = _SLACK_CONTEXT.get("thread_ts")
    channel = _SLACK_CONTEXT.get("channel")

    if not thread_ts or not channel:
        return (
            f"[install_package error: Human approval is required before installing '{pip_target}'. "
            f"No Slack context available. This tool must be called from a Slack session. "
            f"(Unit tests and other non-Slack environments cannot install packages.)"
        )

    from nodes.human_gate import (
        register_confirmation,
        resolve_confirmation,
        get_decision,
        clear_confirmation,
        build_confirmation_blocks,
    )

    client = _SLACK_CONTEXT.get("client")
    if not client:
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
        f"📦 *Python Package Install Request*\n\n"
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

    # ── Human approved — run pip install ─────────────────────────────
    logger.info(f"[install_package] Approved. Installing '{pip_target}' into '{target_env}'.")

    if active_conda == PROD_ENV_NAME:
        py_exe = get_gemma_test_python_exe()
        cmd = [py_exe, "-m", "pip", "install", pip_target]
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
    if active_conda == PROD_ENV_NAME:
        py_exe = get_gemma_test_python_exe()
        verify_cmd = [py_exe, "-c", f"import {import_name}"]
    else:
        verify_cmd = [sys.executable, "-c", f"import {import_name}"]
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


def _install_npm_package(package_name: str, version: str, path: str) -> str:
    """Install a JS/TS package via npm with human approval."""
    # Build the npm install target string
    if version:
        npm_target = f"{package_name}@{version}"
    else:
        npm_target = package_name

    # Resolve the project path
    from tools.coding_tools import _resolve_tool_path
    project_root = _resolve_tool_path(path)

    if not project_root.exists():
        return f"[install_package error: Project path not found: {project_root}]"

    if not (project_root / "package.json").exists():
        logger.warning(f"[install_package] No package.json in {project_root}, npm will create one")

    # ── Require Slack human confirmation ──────────────────────────────
    thread_ts = _SLACK_CONTEXT.get("thread_ts")
    channel = _SLACK_CONTEXT.get("channel")

    if not thread_ts or not channel:
        return (
            f"[install_package error: Human approval is required before installing '{npm_target}'. "
            f"No Slack context available. This tool must be called from a Slack session.]"
        )

    from nodes.human_gate import (
        register_confirmation,
        resolve_confirmation,
        get_decision,
        clear_confirmation,
        build_confirmation_blocks,
    )

    client = _SLACK_CONTEXT.get("client")
    if not client:
        try:
            from slack_sdk import WebClient
            slack_token = os.environ.get("Bot_User_OAuth_Token", "")
            if not slack_token:
                return "[install_package error: Bot_User_OAuth_Token not set — cannot post confirmation message.]"
            client = WebClient(token=slack_token)
        except ImportError:
            return "[install_package error: slack_sdk not installed — cannot post confirmation message.]"

    pending_action = (
        f"📦 *JS/TS Package Install Request (npm)*\n\n"
        f"The coding agent wants to install:\n"
        f"```npm install {npm_target}```\n\n"
        f"Will install into: `{project_root}`\n\n"
        f"Approve to proceed, Reject to cancel."
    )

    event = register_confirmation(thread_ts)

    try:
        blocks = build_confirmation_blocks(pending_action, thread_ts)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"📦 The coding agent wants to install `{npm_target}` via npm. Approve?",
            blocks=blocks,
        )
        logger.info(f"[install_package] Posted npm confirmation for '{npm_target}' to {thread_ts}")
    except Exception as e:
        clear_confirmation(thread_ts)
        return f"[install_package error: Could not post confirmation to Slack: {e}]"

    # Wait for human response
    responded = event.wait(timeout=HUMAN_CONFIRMATION_TIMEOUT)
    decision  = get_decision(thread_ts) if responded else "rejected"

    if not responded:
        logger.warning(f"[install_package] Confirmation timed out for '{npm_target}' — defaulting to rejected.")

    clear_confirmation(thread_ts)

    if not decision or decision.startswith("rejected"):
        reason = decision[len("rejected:"):].strip() if decision and ":" in decision else ""
        msg = f"[install_package: Install of '{npm_target}' rejected by human."
        if reason:
            msg += f" Feedback: {reason}"
        msg += " No packages were installed.]"
        return msg

    # ── Human approved — run npm install ─────────────────────────────
    logger.info(f"[install_package] Approved. Installing '{npm_target}' via npm in '{project_root}'.")

    import platform
    cmd = ["npm", "install", npm_target]
    use_shell = platform.system() == "Windows"

    try:
        command = subprocess.list2cmdline(cmd) if isinstance(cmd, list) and not use_shell else (" ".join(cmd) if use_shell else cmd)
        proc = subprocess.Popen(
            command,
            cwd=str(project_root),
            shell=use_shell,
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
            proc.wait(timeout=120)
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

        rc = proc.returncode
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    except Exception as e:
        return f"[install_package error: npm install failed for '{npm_target}': {e}]"

    output = stdout or stderr
    if rc != 0:
        return (
            f"[install_package error: npm install failed for '{npm_target}'.\n"
            f"Exit code: {rc}\n"
            f"Output:\n{output[:3000]}]"
        )

    return (
        f"install_package: ✓ Installed '{npm_target}' via npm into '{project_root}'\n"
        f"npm output (last 1000 chars):\n{output[-1000:]}"
    )
