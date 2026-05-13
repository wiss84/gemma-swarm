"""
Gemma Swarm — Coding Agent Slack Handlers (Phase 3, Step 3.2)
==============================================================
Handles the full Coding Agent Slack UX:

  Buttons / Modals:
    coding_new_project          → opens new-project modal
    coding_new_project_modal    → creates workspace, starts session
    coding_existing_select      → selects existing workspace, starts session
    open_coding_settings        → opens Coding Settings modal
    coding_settings_modal       → saves settings

  Session runner:
    run_coding_session_slack()  → background thread entry point
                                  invokes CodingAgent via the graph,
                                  posts tool-status updates to Slack,
                                  handles ⏹ Stop button

  Settings persisted in coding_session_settings.json:
    human_gate_bypass           — skip Approve/Reject for destructive tools
    agent_notes_enabled         — enables read_agent_notes and write_agent_note tools
    max_tool_iterations         — override default 30
    model_override              — swap main agent model for this session

  Confirmation blocks (new_integrations.md §1):
    build_coding_confirmation_blocks() — structured reason + tool name display
"""

import json
import logging
import os
import stat
import re
import shutil
import subprocess
import threading
from pathlib import Path

from agents_utils.config import PROJECT_ROOT, CODING_WORKSPACE_ROOT, HUMAN_CONFIRMATION_TIMEOUT
from tools.env_tools import set_coding_slack_context
from coding_agent.graph import (
    run_coding_session,
    register_status_callback,
    unregister_status_callback,
    set_stream_manager as set_coding_stream_manager,
)
from agents_utils.memory import list_workspaces
from slack_utils.thread_state import (
    get_thread_state,
    post_status,
    delete_status,
    update_status,
)
from slack_utils.rate_callbacks import make_wait_callback, make_retry_callback
from slack_utils.stream_manager import StreamManager

logger = logging.getLogger(__name__)

# Module-level rate-limit wait and server-error retry callbacks for the coding agent.
_coding_rate_wait_callback   = None
_coding_rate_retry_callback  = None


def set_coding_rate_callback(callback):
    global _coding_rate_wait_callback
    _coding_rate_wait_callback = callback


def clear_coding_rate_callback():
    global _coding_rate_wait_callback
    _coding_rate_wait_callback = None


def get_coding_rate_callback():
    return _coding_rate_wait_callback


def set_coding_retry_callback(callback):
    global _coding_rate_retry_callback
    _coding_rate_retry_callback = callback


def clear_coding_retry_callback():
    global _coding_rate_retry_callback
    _coding_rate_retry_callback = None


def get_coding_retry_callback():
    return _coding_rate_retry_callback


SETTINGS_FILE         = PROJECT_ROOT / "coding_session_settings.json"
CODING_REGISTRY_FILE  = PROJECT_ROOT / "coding_thread_registry.json"

AVAILABLE_MODELS = [
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
]


def _handle_remove_readonly(func, path, excinfo):
    os.chmod(path, stat.S_IWRITE)
    func(path)

# ── Coding workspace helpers ──────────────────────────────────────────────────

_GIT_AVAILABLE: bool | None = None

def _check_git_available() -> bool:
    global _GIT_AVAILABLE
    if _GIT_AVAILABLE is not None:
        return _GIT_AVAILABLE
    try:
        result = subprocess.run(["git", "--version"], capture_output=True, timeout=5)
        _GIT_AVAILABLE = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _GIT_AVAILABLE = False
    logger.info(f"[coding] git available: {_GIT_AVAILABLE}")
    return _GIT_AVAILABLE


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name.strip()).lower()


def create_coding_workspace(workspace_name: str) -> dict:
    safe = _safe_name(workspace_name)
    workspace_root = CODING_WORKSPACE_ROOT / safe
    workspace_root.mkdir(parents=True, exist_ok=True)
    project_dir = workspace_root

    git_enabled = _check_git_available()
    if git_enabled:
        git_dir = workspace_root / ".git"
        if not git_dir.exists():
            try:
                subprocess.run(["git", "init", str(workspace_root)], capture_output=True, timeout=10)
                subprocess.run(
                    ["git", "-C", str(workspace_root), "commit", "--allow-empty", "-m", "Initial workspace commit"],
                    capture_output=True, timeout=10,
                    env={**os.environ, "GIT_AUTHOR_NAME": "Gemma Swarm",
                         "GIT_AUTHOR_EMAIL": "agent@gemma-swarm",
                         "GIT_COMMITTER_NAME": "Gemma Swarm",
                         "GIT_COMMITTER_EMAIL": "agent@gemma-swarm"},
                )
                gitignore_path = workspace_root / ".gitignore"
                if not gitignore_path.exists():
                    gitignore_path.write_text("**/.git/\n", encoding="utf-8")
                logger.info(f"[coding] git init: {workspace_root}")
            except Exception as e:
                logger.warning(f"[coding] git init failed: {e}")
                git_enabled = False

    return {
        "workspace_root": str(workspace_root),
        "project_dir":    str(project_dir),
        "git_enabled":    git_enabled,
    }


def _import_into_project_dir(source: str, project_dir: Path) -> tuple[str | None, Path | None]:
    source = source.strip()
    if not source:
        return None, None

    if source.startswith("https://github.com") or source.startswith("http://github.com"):
        repo_url = source.rstrip("/")
        if repo_url.endswith(".git"):
            repo_url = repo_url[:-4]
        subfolder_name = Path(repo_url).name
    else:
        local = Path(source)
        subfolder_name = local.name

    if not subfolder_name:
        return "Could not determine project name from source", None

    safe_subfolder_name = _safe_name(subfolder_name)
    import_target = project_dir / safe_subfolder_name
    import_target.mkdir(parents=True, exist_ok=True)

    if source.startswith("https://github.com") or source.startswith("http://github.com"):
        if not _check_git_available():
            return "git is not installed or not in PATH. Cannot clone GitHub repos.", None
        clone_url = source if source.endswith(".git") else source + ".git"
        try:
            result = subprocess.run(
                ["git", "clone", clone_url, str(import_target)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                return f"git clone failed: {result.stderr.strip()[:200]}", None
            nested_gitignore = import_target / ".gitignore"
            if not nested_gitignore.exists():
                nested_gitignore.write_text(".git/\n", encoding="utf-8")
            return None, import_target
        except subprocess.TimeoutExpired:
            return "git clone timed out (120s). Try a smaller repository.", None
        except Exception as e:
            return f"git clone error: {e}", None

    local = Path(source).resolve()
    if not local.exists():
        return f"Path not found: {source}", None
    if not local.is_dir():
        return f"Not a directory: {source}", None
    try:
        local.relative_to(PROJECT_ROOT.resolve())
        return (
            f"Cannot import '{local.name}': that path is inside the gemma_swarm project folder. "
            "Copy your project somewhere else first.",
            None,
        )
    except ValueError:
        pass
    try:
        ignore_list = shutil.ignore_patterns('node_modules', '__pycache__', '.venv', 'venv', '.next', 'dist', '.DS_Store')
        shutil.copytree(str(local), str(import_target), dirs_exist_ok=True, ignore=ignore_list)
        return None, import_target
    except Exception as e:
        return f"Copy failed: {e}", None


# ── Coding thread registry ────────────────────────────────────────────────────

def _load_coding_registry() -> dict:
    if not CODING_REGISTRY_FILE.exists():
        return {}
    try:
        return json.loads(CODING_REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_coding_registry_entry(thread_ts, workspace_root, project_dir, project_name, channel_id):
    try:
        registry = _load_coding_registry()
        registry[thread_ts] = {
            "workspace_root": workspace_root,
            "project_dir":    project_dir,
            "project_name":   project_name,
            "channel_id":     channel_id,
        }
        CODING_REGISTRY_FILE.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[coding] Could not save registry: {e}")


def list_coding_workspaces() -> list[str]:
    registry = _load_coding_registry()
    seen = {}
    for ts, data in registry.items():
        name = data.get("project_name", "")
        if name and name not in seen:
            seen[name] = float(ts) if ts.replace(".", "").isdigit() else 0
    return sorted(seen, key=lambda n: seen[n], reverse=True)

# ── Settings helpers ───────────────────────────────────────────────────────────

def load_coding_settings() -> dict:
    defaults = {
        "human_gate_bypass":    False,
        "agent_notes_enabled":  True,
        "max_tool_iterations":  30,
        "model_override":       "",
    }
    if not SETTINGS_FILE.exists():
        return defaults
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return {**defaults, **saved}
    except Exception as e:
        logger.warning(f"[coding] Could not load settings: {e}")
        return defaults


def save_coding_settings(settings: dict):
    try:
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"[coding] Could not save settings: {e}")


# ── Block / modal builders ─────────────────────────────────────────────────────

def build_coding_new_project_modal(thread_ts: str, trigger_id: str) -> dict:
    return {
        "trigger_id": trigger_id,
        "view": {
            "type":             "modal",
            "callback_id":      "coding_new_project_modal",
            "private_metadata": thread_ts,
            "title":            {"type": "plain_text", "text": "New Coding Project"},
            "submit":           {"type": "plain_text", "text": "Create"},
            "close":            {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input", "block_id": "coding_project_name_block",
                    "label": {"type": "plain_text", "text": "Project Name"},
                    "element": {
                        "type": "plain_text_input", "action_id": "coding_project_name_input",
                        "placeholder": {"type": "plain_text", "text": "e.g. my-flask-app"},
                        "min_length": 2, "max_length": 50,
                    },
                    "hint": {"type": "plain_text", "text": "Letters, numbers, and hyphens only."},
                },
                {"type": "section", "text": {"type": "mrkdwn", "text": "*Optional: Import existing code*"}},
                {
                    "type": "input", "block_id": "coding_project_path_block", "optional": True,
                    "label": {"type": "plain_text", "text": "Local path or GitHub URL"},
                    "element": {
                        "type": "plain_text_input", "action_id": "coding_project_path_input",
                        "placeholder": {"type": "plain_text", "text": "e.g. C:\\Users\\you\\myproject  or  https://github.com/you/repo"},
                    },
                    "hint": {"type": "plain_text", "text": "Local path → copied into workspace. GitHub URL → cloned.\nPlease Wait 10-30 seconds for large projects to load successfully."},
                },
            ],
        },
    }


def build_coding_settings_modal(trigger_id: str) -> dict:
    s = load_coding_settings()
    bypass_initial = "bypass" if s["human_gate_bypass"] else "require"
    notes_initial  = "enabled" if s["agent_notes_enabled"] else "disabled"
    iters_initial  = str(s["max_tool_iterations"])
    model_initial  = s["model_override"] or AVAILABLE_MODELS[0]

    return {
        "trigger_id": trigger_id,
        "view": {
            "type": "modal", "callback_id": "coding_settings_modal",
            "title": {"type": "plain_text", "text": "🛠️ Coding Settings"},
            "submit": {"type": "plain_text", "text": "Save"},
            "close":  {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": "*1. Destructive operation approval*\nWhen bypassed, `install_package` run without asking."}},
                {
                    "type": "input", "block_id": "coding_gate_block",
                    "label": {"type": "plain_text", "text": "Approval mode"},
                    "element": {
                        "type": "static_select", "action_id": "coding_gate_select",
                        "initial_option": {"text": {"type": "plain_text", "text": "Bypass (long run / unattended)" if s["human_gate_bypass"] else "Require approval (default)"}, "value": bypass_initial},
                        "options": [
                            {"text": {"type": "plain_text", "text": "Require approval (default)"}, "value": "require"},
                            {"text": {"type": "plain_text", "text": "Bypass (long run / unattended)"}, "value": "bypass"},
                        ],
                    },
                },
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "*2. Agent learning notes*\nThe agent writes short notes about mistakes and codebase patterns. Loaded at session start."}},
                {
                    "type": "input", "block_id": "coding_notes_block",
                    "label": {"type": "plain_text", "text": "Agent self-notes"},
                    "element": {
                        "type": "static_select", "action_id": "coding_notes_select",
                        "initial_option": {"text": {"type": "plain_text", "text": "Enabled (default)" if s["agent_notes_enabled"] else "Disabled"}, "value": notes_initial},
                        "options": [
                            {"text": {"type": "plain_text", "text": "Enabled (default)"}, "value": "enabled"},
                            {"text": {"type": "plain_text", "text": "Disabled"}, "value": "disabled"},
                        ],
                    },
                },
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "*3. Max tool iterations*\nHow many tool calls the agent may make per session. Increase for long unattended runs."}},
                {
                    "type": "input", "block_id": "coding_iters_block",
                    "label": {"type": "plain_text", "text": "Max iterations (10–1000)"},
                    "element": {"type": "plain_text_input", "action_id": "coding_iters_input", "initial_value": iters_initial, "placeholder": {"type": "plain_text", "text": "30"}},
                },
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "*4. Model override*\nSwitch the main agent model for this session. Useful when quota is low."}},
                {
                    "type": "input", "block_id": "coding_model_block",
                    "label": {"type": "plain_text", "text": "Main agent model"},
                    "element": {
                        "type": "static_select", "action_id": "coding_model_select",
                        "initial_option": {"text": {"type": "plain_text", "text": model_initial}, "value": model_initial},
                        "options": [{"text": {"type": "plain_text", "text": m}, "value": m} for m in AVAILABLE_MODELS],
                    },
                },
            ],
        },
    }


def build_coding_confirmation_blocks(tool_name, reason, impact, thread_ts) -> list:
    text = (
        f"⚠️ *Confirmation Required*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"*Tool:*   `{tool_name}`\n"
        f"*Reason:* {reason}\n"
        f"*Impact:* {impact}"
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "✅ Approve", "emoji": True}, "style": "primary", "action_id": "confirm_approve", "value": thread_ts},
                {"type": "button", "text": {"type": "plain_text", "text": "❌ Reject", "emoji": True}, "style": "danger", "action_id": "confirm_reject", "value": thread_ts},
            ],
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_No response in {HUMAN_CONFIRMATION_TIMEOUT // 60} minutes → defaults to Reject_"}]},
    ]


# ── Session runner ─────────────────────────────────────────────────────────────

def run_coding_session_slack(
    prompt: str,
    thread_ts: str,
    channel: str,
    client,
    say,
    workspace_path: str,
    project_name: str,
    session_id: str,
):
    from coding_agent.graph import run_coding_session
    from agents_utils.context_ui_launcher import launch_context_ui

    set_coding_slack_context(thread_ts=thread_ts, channel=channel, client=client)

    wait_cb  = make_wait_callback(client, channel, thread_ts)
    retry_cb = make_retry_callback(client, channel, thread_ts)
    set_coding_rate_callback(wait_cb)
    set_coding_retry_callback(retry_cb)

    state    = get_thread_state(thread_ts)
    settings = load_coding_settings()

    max_iters = settings.get("max_tool_iterations", 30)
    try:
        max_iters = max(10, min(1000, int(max_iters)))
    except (ValueError, TypeError):
        max_iters = 30

    model_override      = settings.get("model_override", "")
    agent_notes_enabled = settings.get("agent_notes_enabled", True)

    # ── Stream manager: live thinking + tool cards ─────────────────────────
    stream_manager = StreamManager(client, channel, thread_ts, user_id=state.user_id)
    stream_manager.open()
    set_coding_stream_manager(session_id, stream_manager)

    # Cycle the animated status text as each tool runs
    def slack_tool_status_fn(tool_name: str):
        readable = tool_name.replace("_", " ").title()
        stream_manager.set_status(f"🔧 {readable}")

    register_status_callback(session_id, slack_tool_status_fn)

    launch_context_ui()

    # Post the Stop button
    stop_btn_ts = ""
    try:
        stop_result = client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text="⏹ Coding session running",
            blocks=[{
                "type": "actions",
                "elements": [{
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⏹ Stop", "emoji": True},
                    "style": "danger",
                    "action_id": "coding_stop",
                    "value": thread_ts,
                }],
            }],
        )
        stop_btn_ts = stop_result.get("ts", "")
    except Exception as e:
        logger.warning(f"[coding] Could not post stop button: {e}")

    try:
        result = run_coding_session(
            prompt=prompt,
            session_id=session_id,
            workspace_path=workspace_path,
            project_name=project_name,
            slack_thread_ts=thread_ts,
            slack_channel=channel,
            cancel_event=state.cancel_event,
            model_override=model_override,
            agent_notes_enabled=agent_notes_enabled,
        )

    except Exception as e:
        logger.error(f"[coding] Session error: {e}", exc_info=True)

        # Store retry state so the Continue button can resume the session
        state.retry_message = prompt
        state.last_error    = str(e)

        error_text    = f"❌ Coding session error: `{e}`"
        button_blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": error_text}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🔄 Continue", "emoji": True},
                        "action_id": "coding_continue_after_error",
                        "value": thread_ts,
                        "style": "primary",
                    }
                ],
            },
        ]
        try:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                blocks=button_blocks, mrkdwn=True,
            )
        except Exception as post_err:
            logger.error(f"[coding] Could not post error with button: {post_err}")
            try:
                client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=error_text)
            except Exception:
                pass
        result = None
    finally:
        unregister_status_callback(session_id)
        clear_coding_rate_callback()
        clear_coding_retry_callback()

        stream_manager.close()
        set_coding_stream_manager(session_id, None)

        state.coding_active = False

        if stop_btn_ts:
            try:
                client.chat_delete(channel=channel, ts=stop_btn_ts)
            except Exception:
                pass

    if state.cancel_event.is_set():
        try:
            client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="⏹ Coding session cancelled.")
        except Exception:
            pass
        return

    if result:
        pending_text: str | None = None

        for item in result:
            if isinstance(item, dict):
                if pending_text is not None:
                    try:
                        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=pending_text, mrkdwn=True)
                    except Exception as e:
                        logger.error(f"[coding] Could not post result: {e}")
                    pending_text = None
                try:
                    client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="Table:", blocks=[item], mrkdwn=True)
                except Exception as e:
                    logger.error(f"[coding] Could not post table block: {e}")
            else:
                if pending_text is not None:
                    try:
                        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=pending_text, mrkdwn=True)
                    except Exception as e:
                        logger.error(f"[coding] Could not post result: {e}")
                pending_text = item

        if pending_text is not None:
            try:
                client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=pending_text, mrkdwn=True)
            except Exception as e:
                logger.error(f"[coding] Could not post result: {e}")
    else:
        try:
            client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="⚠️ Coding session completed but returned no output.")
        except Exception:
            pass


def _start_coding_session(thread_ts, channel, workspace_path, project_name, client, say, prompt=""):
    state = get_thread_state(thread_ts)
    state.workspace_path   = workspace_path
    state.project_name     = project_name
    state.coding_mode      = True
    state.coding_active    = False
    state.coding_status_ts = ""

    if state.workspace_msg_ts:
        try:
            client.chat_update(
                channel=channel, ts=state.workspace_msg_ts,
                text=f"💻 Coding Agent — project: *{project_name}*\n_Type your coding task in this thread._",
                blocks=[],
            )
        except Exception as e:
            logger.warning(f"[coding] Could not clean up entry message: {e}")

    pending = prompt or state.pending_message
    if pending:
        state.pending_message = ""
        state.coding_active   = True
        state.cancel_event    = threading.Event()
        threading.Thread(
            target=run_coding_session_slack,
            args=(pending, thread_ts, channel, client, say, workspace_path, project_name, thread_ts),
            daemon=True,
        ).start()


# ── Register handlers ──────────────────────────────────────────────────────────

def register_coding_handlers(app, run_coding_fn=None):

    @app.action("coding_new_project")
    def handle_coding_new_project(ack, body, client):
        ack()
        trigger_id = body["trigger_id"]
        thread_ts  = body["actions"][0]["value"]
        try:
            client.views_open(**build_coding_new_project_modal(thread_ts, trigger_id))
        except Exception as e:
            logger.error(f"[coding] Could not open new project modal: {e}")

    @app.view("coding_new_project_modal")
    def handle_coding_new_project_submit(ack, body, client, say):
        ack()
        thread_ts = body["view"]["private_metadata"]
        values    = body["view"]["state"]["values"]

        project_name = (values.get("coding_project_name_block", {}).get("coding_project_name_input", {}).get("value", "") or "").strip()
        project_path = (values.get("coding_project_path_block", {}).get("coding_project_path_input", {}).get("value", "") or "").strip()

        if not project_name:
            return

        state   = get_thread_state(thread_ts)
        channel = state.pending_channel or state.active_channel

        ws = create_coding_workspace(project_name)
        workspace_root_str = ws["workspace_root"]
        workspace_root = Path(workspace_root_str)
        project_dir = workspace_root

        if project_path:
            error, actual_import_path = _import_into_project_dir(project_path, workspace_root)
            if error:
                try:
                    client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f"⚠️ Project created but import failed: {error}\nYou can add files manually.")
                except Exception:
                    pass
            else:
                project_dir = actual_import_path
                try:
                    client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f"✅ Successfully imported: `{project_dir.name}/`.")
                except Exception:
                    pass

        _save_coding_registry_entry(thread_ts, workspace_root_str, str(project_dir), project_name, channel)
        _start_coding_session(thread_ts, channel, workspace_root_str, project_name, client, say)

    @app.action("coding_existing_select")
    def handle_coding_existing_select(ack, body, client, say):
        ack()
        project_name = body["actions"][0]["selected_option"]["value"]
        thread_ts    = body["container"]["thread_ts"]
        channel      = body["channel"]["id"]

        if not project_name:
            return

        registry = _load_coding_registry()
        entry = None
        if thread_ts in registry:
            entry = registry[thread_ts]
        else:
            for e in registry.values():
                if e.get("project_name") == project_name:
                    entry = e
                    break

        if entry:
            workspace_root = entry.get("workspace_root", "")
            project_dir    = entry.get("project_dir", workspace_root)
        else:
            workspace_root = str(CODING_WORKSPACE_ROOT / _safe_name(project_name))
            project_dir    = workspace_root

        _save_coding_registry_entry(thread_ts, workspace_root, project_dir, project_name, channel)
        _start_coding_session(thread_ts, channel, workspace_root, project_name, client, say)

    @app.action("open_coding_settings")
    def handle_open_coding_settings(ack, body, client):
        ack()
        try:
            client.views_open(**build_coding_settings_modal(body["trigger_id"]))
        except Exception as e:
            logger.error(f"[coding] Could not open settings modal: {e}")

    @app.view("coding_settings_modal")
    def handle_coding_settings_submit(ack, body, client):
        ack()
        values = body["view"]["state"]["values"]

        gate_val  = (values.get("coding_gate_block",  {}).get("coding_gate_select",  {}).get("selected_option", {}) or {}).get("value", "require")
        notes_val = (values.get("coding_notes_block", {}).get("coding_notes_select", {}).get("selected_option", {}) or {}).get("value", "enabled")
        iters_val = (values.get("coding_iters_block", {}).get("coding_iters_input",  {}) or {}).get("value", "30")
        model_val = (values.get("coding_model_block", {}).get("coding_model_select", {}).get("selected_option", {}) or {}).get("value", "")

        try:
            max_iters = max(10, min(1000, int(iters_val)))
        except (ValueError, TypeError):
            max_iters = 30

        settings = {
            "human_gate_bypass":   gate_val == "bypass",
            "agent_notes_enabled": notes_val == "enabled",
            "max_tool_iterations": max_iters,
            "model_override":      model_val if model_val in AVAILABLE_MODELS else "",
        }
        save_coding_settings(settings)
        logger.info(f"[coding] Settings saved: {settings}")

    @app.action("coding_stop")
    def handle_coding_stop(ack, body, client):
        ack()
        thread_ts = body["actions"][0]["value"]
        state     = get_thread_state(thread_ts)
        channel   = body["channel"]["id"]
        try:
            result = client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="⏹ Stopping coding session...")
            state.stop_ack_ts = result.get("ts", "")
            state.stop_ack_channel = channel
        except Exception as e:
            logger.warning(f"[coding] Stop post failed: {e}")
            state.stop_ack_ts = ""
            state.stop_ack_channel = ""
        if state.cancel_event:
            state.cancel_event.set()

    @app.action("coding_continue_after_error")
    def handle_coding_continue_after_error(ack, body, client, say):
        """Resume the coding session after a transient server error."""
        ack()
        thread_ts  = body["actions"][0]["value"]
        channel_id = body["channel"]["id"]
        state      = get_thread_state(thread_ts)

        if getattr(state, "coding_active", False):
            try:
                client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text="⚠️ Coding session is already running. Please wait.",
                )
            except Exception:
                pass
            return

        retry_prompt = getattr(state, "retry_message", "")
        if not retry_prompt:
            try:
                client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts,
                    text="✅ No pending coding error to retry.",
                )
            except Exception:
                pass
            return

        state.retry_message  = ""
        state.last_error     = ""
        state.coding_active  = True
        state.cancel_event   = threading.Event()

        workspace_path = getattr(state, "workspace_path", "")
        project_name   = getattr(state, "project_name", "")

        threading.Thread(
            target=run_coding_session_slack,
            args=(retry_prompt, thread_ts, channel_id, client, say,
                  workspace_path, project_name, thread_ts),
            daemon=True,
        ).start()
