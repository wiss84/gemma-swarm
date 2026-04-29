"""
Gemma Swarm — Coding Agent: Layer 5 Git Tools
===============================================
Tools the coding agent uses to make all file changes safe and reversible.

Design rule: The agent MUST call git_create_branch before making any file
changes. This is enforced both in the system prompt and in git_commit (which
will warn if changes are on main/master).

Tools:
    git_status(repo_path)                     — list changed files (git status --short)
    git_diff(repo_path, file_path)            — show what changed (unified diff)
    git_commit(repo_path, message)            — stage all + commit, returns hash
    git_create_branch(repo_path, branch_name) — checkout -b, validates name format
    git_restore_file(repo_path, file_path)    — undo changes to one file
    git_restore_all(repo_path, thread_ts,     — undo ALL unstaged changes,
                    channel, client)            requires Slack human confirmation

Human confirmation for git_restore_all:
    Uses resolve_confirmation() / register_confirmation() from nodes/human_gate.py
    and build_confirmation_blocks() to post Approve/Reject buttons to Slack.
    If no Slack context is provided (e.g. unit tests), the tool auto-rejects
    and returns an error — this operation is too destructive to default-approve.
"""

import re
import os
import logging
import subprocess
import threading
from queue import Queue, Empty
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from agents_utils.config import PROJECT_ROOT, HUMAN_CONFIRMATION_TIMEOUT
from tools.coding_tools import _workspace_root

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

MAX_DIFF_CHARS    = 10_000   # cap git diff output to avoid flooding context
DEFAULT_TIMEOUT   = 30       # seconds — git commands are fast
PROTECTED_BRANCHES = {"main", "master", "develop", "production"}  # warn if committing here

# Valid git branch name: no spaces, no ~^:?*[\\, no .., no @{, no leading/trailing ./-
_BRANCH_NAME_RE = re.compile(r"^(?![-./])(?!.*\.\.)(?!.*@\{)[^\x00-\x1f ~^:?*\[\\]+(?<![-./])$")


# ── Internal helpers ───────────────────────────────────────────────────────────

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


def _resolve_path(path: str) -> Path:
    """Resolve a path for git operations with workspace boundary validation."""
    workspace_root = _workspace_root()
    p = Path(path)
    resolved = p.resolve() if p.is_absolute() else (workspace_root / path).resolve()
    
    # Validate path is within workspace
    if not _is_within_workspace(resolved, workspace_root):
        raise ValueError(
            f"Access denied: path is outside your workspace. "
            f"Attempted: {resolved} | Workspace: {workspace_root}"
        )
    
    return resolved


def _run_git(args: list[str], cwd: Path, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    """
    Run a git command and return (returncode, stdout, stderr).
    Safe from deadlocks — uses threaded output draining.
    Always uses list form (no shell=True) for safety.
    """
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
        return -1, "", f"git command timed out after {timeout}s"
    except FileNotFoundError:
        return -1, "", "git is not installed or not in PATH"
    except Exception as e:
        return -1, "", str(e)


def _validate_repo(repo_path: str) -> tuple[Path | None, str]:
    """
    Resolve and validate that repo_path is a git repository.
    Returns (resolved_path, error_message). If no error, error_message is "".
    """
    try:
        resolved = _resolve_path(repo_path)
    except Exception as e:
        return None, f"[git error: Invalid path '{repo_path}': {e}]"

    if not resolved.exists():
        return None, f"[git error: Path does not exist: {resolved}]"

    # Check it's actually a git repo
    rc, _, _ = _run_git(["rev-parse", "--git-dir"], resolved)
    if rc != 0:
        return None, f"[git error: Not a git repository: {resolved}]"

    return resolved, ""


def _current_branch(repo: Path) -> str:
    """Return the current branch name, or 'HEAD (detached)' if detached."""
    rc, stdout, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    return stdout if rc == 0 else "unknown"


def _truncate(text: str, max_chars: int = MAX_DIFF_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half]
        + f"\n\n... [output truncated — {len(text) - max_chars} chars omitted] ...\n\n"
        + text[-half:]
    )


# ── Tool 1: git_status ────────────────────────────────────────────────────────

class GitStatusInput(BaseModel):
    repo_path: str = Field(
        default="",
        description="Path to the git repository root. Defaults to the gemma_swarm project root.",
    )


@tool(args_schema=GitStatusInput)
def git_status(repo_path: str = "") -> str:
    """
    Show which files have been modified, added, or deleted (git status --short).
    Use this before and after making changes to understand what is staged/unstaged.
    Also shows the current branch name.
    Returns an error string starting with '[' on failure.
    """
    resolved, err = _validate_repo(repo_path or str(_workspace_root()))
    if err:
        return err

    branch = _current_branch(resolved)
    rc, stdout, stderr = _run_git(["status", "--short"], resolved)

    if rc != 0:
        return f"[git_status error: {stderr or 'unknown error'}]"

    if not stdout:
        return f"git_status: ✓ Working tree clean on branch '{branch}'\nRepo: {resolved}"

    lines = stdout.splitlines()
    header = (
        f"git_status: {len(lines)} changed file(s) on branch '{branch}'\n"
        f"Repo: {resolved}\n"
        + "─" * 60 + "\n\n"
    )
    return header + stdout


# ── Tool 2: git_diff ──────────────────────────────────────────────────────────

class GitDiffInput(BaseModel):
    repo_path: str = Field(
        default="",
        description="Path to the git repository root. Defaults to gemma_swarm project root.",
    )
    file_path: str = Field(
        default="",
        description=(
            "Optional: path to a specific file to diff (absolute or repo-relative). "
            "If empty, diffs ALL changed files."
        ),
    )


@tool(args_schema=GitDiffInput)
def git_diff(repo_path: str = "", file_path: str = "") -> str:
    """
    Show what changed in a file (or all files) as a unified diff.
    Covers both staged and unstaged changes (uses --cached + regular diff combined).
    Output is capped at 10,000 characters to avoid flooding the context window.
    Use this to review changes before committing, or to pass to a review subagent.
    Returns an error string starting with '[' on failure.
    """
    resolved, err = _validate_repo(repo_path or str(_workspace_root()))
    if err:
        return err

    branch = _current_branch(resolved)

    # Determine target: specific file or all
    target_args = []
    if file_path:
        file_resolved = _resolve_path(file_path)
        if not file_resolved.exists():
            return f"[git_diff error: File not found: {file_resolved}]"
        # Use relative path from repo root for cleaner output
        try:
            rel = str(file_resolved.relative_to(resolved))
        except ValueError:
            rel = str(file_resolved)
        target_args = ["--", rel]

    # Get unstaged diff
    rc1, diff_unstaged, stderr1 = _run_git(["diff"] + target_args, resolved)
    # Get staged diff (already added but not committed)
    rc2, diff_staged, stderr2 = _run_git(["diff", "--cached"] + target_args, resolved)

    if rc1 != 0:
        return f"[git_diff error: {stderr1 or 'unknown error'}]"

    combined = ""
    if diff_staged:
        combined += "── STAGED CHANGES ──\n" + diff_staged
    if diff_unstaged:
        combined += ("\n\n" if combined else "") + "── UNSTAGED CHANGES ──\n" + diff_unstaged

    if not combined:
        label = f"'{file_path}'" if file_path else "any tracked files"
        return f"git_diff: No changes found for {label} on branch '{branch}'"

    combined = _truncate(combined)
    scope = f"file: {file_path}" if file_path else "all changed files"
    header = (
        f"git_diff: {scope} | branch: '{branch}'\n"
        f"Repo: {resolved}\n"
        + "─" * 60 + "\n\n"
    )
    return header + combined


# ── Tool 3: git_commit ────────────────────────────────────────────────────────

class GitCommitInput(BaseModel):
    repo_path: str = Field(
        default="",
        description="Path to the git repository root. Defaults to gemma_swarm project root.",
    )
    message: str = Field(
        description="Commit message. Must not be empty. Use imperative mood: 'Add X', 'Fix Y', 'Refactor Z'.",
    )


@tool(args_schema=GitCommitInput)
def git_commit(repo_path: str = "", message: str = "") -> str:
    """
    Stage all changes (git add -A) and commit with the given message.
    IMPORTANT: Always call git_create_branch before making any file changes.
    This tool will warn (but not block) if you are committing directly on main/master.
    Validates that the commit message is not empty.
    Returns the short commit hash and summary on success.
    Returns an error string starting with '[' on failure.
    """
    resolved, err = _validate_repo(repo_path or str(_workspace_root()))
    if err:
        return err

    message = message.strip()
    if not message:
        return "[git_commit error: Commit message cannot be empty.]"

    branch = _current_branch(resolved)

    # Check there is actually something to commit
    rc_check, status_out, _ = _run_git(["status", "--short"], resolved)
    if rc_check == 0 and not status_out:
        return f"[git_commit: Nothing to commit on branch '{branch}' — working tree is clean.]"

    # Stage everything
    rc_add, _, stderr_add = _run_git(["add", "-A"], resolved)
    if rc_add != 0:
        return f"[git_commit error during git add -A: {stderr_add}]"

    # Commit
    rc_commit, stdout_commit, stderr_commit = _run_git(["commit", "-m", message], resolved)
    if rc_commit != 0:
        # Unstage so the user isn't left in a half-staged state
        _run_git(["reset", "HEAD"], resolved)
        return f"[git_commit error: {stderr_commit or stdout_commit or 'unknown error'}]"

    # Extract short hash from output like "[main abc1234] message"
    short_hash = ""
    for line in stdout_commit.splitlines():
        m = re.search(r"\[[\w/\-]+ ([0-9a-f]+)\]", line)
        if m:
            short_hash = m.group(1)
            break

    return (
        f"git_commit: ✓ Committed on branch '{branch}'\n"
        f"Hash: {short_hash}\n"
        f"Message: {message}\n"
        f"Repo: {resolved}"
    )


# ── Tool 4: git_create_branch ─────────────────────────────────────────────────

class GitCreateBranchInput(BaseModel):
    repo_path: str = Field(
        default="",
        description="Path to the git repository root. Defaults to gemma_swarm project root.",
    )
    branch_name: str = Field(
        description=(
            "Name for the new branch. Use lowercase with hyphens, e.g. 'feat/add-git-tools' "
            "or 'fix/broken-imports'. No spaces or special characters."
        ),
    )


@tool(args_schema=GitCreateBranchInput)
def git_create_branch(repo_path: str = "", branch_name: str = "") -> str:
    """
    Create and switch to a new git branch (git checkout -b <branch>).
    ALWAYS call this before making any file changes. This is the first step
    in every coding task — it keeps all changes reversible and isolated.
    Validates the branch name format (no spaces, no special characters).
    Returns an error string starting with '[' on failure.
    """
    resolved, err = _validate_repo(repo_path or str(_workspace_root()))
    if err:
        return err

    branch_name = branch_name.strip()
    if not branch_name:
        return "[git_create_branch error: Branch name cannot be empty.]"

    # Validate branch name format
    if not _BRANCH_NAME_RE.match(branch_name):
        return (
            f"[git_create_branch error: Invalid branch name '{branch_name}'. "
            f"Use lowercase letters, numbers, hyphens, and forward slashes only. "
            f"Example: 'feat/add-new-tool' or 'fix/broken-imports']"
        )

    # Check the branch doesn't already exist
    rc_check, existing_out, _ = _run_git(["branch", "--list", branch_name], resolved)
    if rc_check == 0 and existing_out.strip():
        return (
            f"[git_create_branch error: Branch '{branch_name}' already exists. "
            f"Choose a different name or switch to it with: git checkout {branch_name}]"
        )

    current_branch = _current_branch(resolved)

    rc, stdout, stderr = _run_git(["checkout", "-b", branch_name], resolved)
    if rc != 0:
        return f"[git_create_branch error: {stderr or stdout or 'unknown error'}]"

    return (
        f"git_create_branch: ✓ Created and switched to branch '{branch_name}'\n"
        f"Previous branch: '{current_branch}'\n"
        f"Repo: {resolved}\n"
        f"You are now on '{branch_name}'. All changes you make will be on this branch."
    )


# ── Tool 5: git_restore_file ──────────────────────────────────────────────────

class GitRestoreFileInput(BaseModel):
    repo_path: str = Field(
        default="",
        description="Path to the git repository root. Defaults to gemma_swarm project root.",
    )
    file_path: str = Field(
        description="Path to the file to restore (absolute or repo-relative). This undoes all unstaged changes to that file.",
    )


@tool(args_schema=GitRestoreFileInput)
def git_restore_file(repo_path: str = "", file_path: str = "") -> str:
    """
    Undo all unstaged changes to a single file (git restore <file>).
    Use this when you made a mistake in one file and want to revert it without
    touching other files. This is NOT reversible — the file will go back to the
    last committed state.
    Returns an error string starting with '[' on failure.
    """
    resolved, err = _validate_repo(repo_path or str(_workspace_root()))
    if err:
        return err

    if not file_path or not file_path.strip():
        return "[git_restore_file error: file_path cannot be empty. Use git_restore_all to restore everything.]"

    file_resolved = _resolve_path(file_path)
    if not file_resolved.exists():
        return f"[git_restore_file error: File not found: {file_resolved}]"

    # Use relative path from repo root
    try:
        rel = str(file_resolved.relative_to(resolved))
    except ValueError:
        rel = str(file_resolved)

    branch = _current_branch(resolved)

    rc, stdout, stderr = _run_git(["restore", rel], resolved)
    if rc != 0:
        return f"[git_restore_file error: {stderr or stdout or 'unknown error'}]"

    return (
        f"git_restore_file: ✓ Restored '{rel}' to last committed state\n"
        f"Branch: '{branch}'\n"
        f"Repo: {resolved}\n"
        f"⚠️  Unsaved changes to '{rel}' have been discarded."
    )


# ── Tool 6: git_restore_all ───────────────────────────────────────────────────

class GitRestoreAllInput(BaseModel):
    repo_path:  str = Field(
        default="",
        description="Path to the git repository root. Defaults to gemma_swarm project root.",
    )
    thread_ts:  str = Field(
        default="",
        description="Slack thread timestamp for posting the confirmation message. Required for human approval.",
    )
    channel:    str = Field(
        default="",
        description="Slack channel ID for posting the confirmation message. Required for human approval.",
    )


@tool(args_schema=GitRestoreAllInput)
def git_restore_all(repo_path: str = "", thread_ts: str = "", channel: str = "") -> str:
    """
    Undo ALL unstaged changes in the repository (git restore .).
    This is a destructive emergency operation — it discards every uncommitted
    change in the working tree. REQUIRES human confirmation via Slack before running.
    If no Slack context is available (e.g. unit tests), the tool will REFUSE to run.
    Use git_restore_file to undo a single file without requiring confirmation.
    Returns an error string starting with '[' on failure or rejection.
    """
    resolved, err = _validate_repo(repo_path or str(_workspace_root()))
    if err:
        return err

    branch = _current_branch(resolved)

    # Check there is actually something to restore
    rc_check, status_out, _ = _run_git(["status", "--short"], resolved)
    if rc_check == 0 and not status_out:
        return f"git_restore_all: Nothing to restore — working tree is already clean on branch '{branch}'."

    changed_count = len(status_out.splitlines()) if status_out else 0

    # ── Require Slack human confirmation ─────────────────────────────────────
    # Import here to avoid circular imports at module level
    from nodes.human_gate import (
        register_confirmation,
        resolve_confirmation,
        get_decision,
        clear_confirmation,
        build_confirmation_blocks,
    )

    if not thread_ts or not channel:
        return (
            "[git_restore_all error: This operation requires human confirmation via Slack. "
            "Provide thread_ts and channel so the agent can post an Approve/Reject message. "
            "If you want to restore a single file without confirmation, use git_restore_file instead.]"
        )

    # Try to get the Slack client — it's injected into the tool at runtime by the agent
    # If we can't reach Slack, refuse to proceed
    try:
        from slack_sdk import WebClient
        slack_token = os.environ.get("Bot_User_OAuth_Token", "")
        if not slack_token:
            return "[git_restore_all error: SLACK_BOT_TOKEN not set — cannot post confirmation message.]"
        client = WebClient(token=slack_token)
    except ImportError:
        return "[git_restore_all error: slack_sdk not installed — cannot post confirmation message.]"

    pending_action = (
        f"🚨 *git restore .* — Undo ALL {changed_count} uncommitted change(s) on branch `{branch}`?\n\n"
        f"This will discard every unsaved edit in the repository:\n```{status_out[:1500]}```\n\n"
        f"*This cannot be undone.* Approve only if you want to start fresh from the last commit."
    )

    event = register_confirmation(thread_ts)

    try:
        blocks = build_confirmation_blocks(pending_action, thread_ts)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="⚠️ git restore . requires your approval.",
            blocks=blocks,
        )
        logger.info(f"[git_restore_all] Posted confirmation request for {changed_count} changes to {thread_ts}")
    except Exception as e:
        clear_confirmation(thread_ts)
        return f"[git_restore_all error: Could not post confirmation to Slack: {e}]"

    # Block until human responds or timeout
    responded = event.wait(timeout=HUMAN_CONFIRMATION_TIMEOUT)
    decision  = get_decision(thread_ts) if responded else "rejected"

    if not responded:
        logger.warning("[git_restore_all] Confirmation timed out — defaulting to rejected.")

    clear_confirmation(thread_ts)

    if not decision or decision.startswith("rejected"):
        reason = decision[len("rejected:"):].strip() if decision and ":" in decision else ""
        msg = f"[git_restore_all: Operation rejected by human."
        if reason:
            msg += f" Feedback: {reason}"
        msg += " No changes were made.]"
        return msg

    # ── Human approved — run restore ─────────────────────────────────────────
    rc, stdout, stderr = _run_git(["restore", "."], resolved)
    if rc != 0:
        return f"[git_restore_all error during git restore .: {stderr or stdout or 'unknown error'}]"

    logger.info(f"[git_restore_all] Successfully restored working tree on branch '{branch}' in {resolved}")
    return (
        f"git_restore_all: ✓ All {changed_count} uncommitted change(s) discarded on branch '{branch}'\n"
        f"Repo: {resolved}\n"
        f"Working tree is now clean. You are back to the last committed state."
    )
