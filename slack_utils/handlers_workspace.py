"""
Gemma Swarm — Workspace & Preferences Handlers
================================================
Block Kit blocks, modal builders, activation logic, and button handlers
for workspace selection and user preferences.
"""

import json
import logging
import os
import threading
import re

from dotenv import load_dotenv
load_dotenv()

from agents_utils.memory import create_workspace, list_workspaces
from agents_utils.config import WORKSPACE_ROOT, USER_PREFERENCES_FILE
from slack_utils.thread_state import get_thread_state, save_thread_workspace, get_project_original_thread, set_current_session

logger = logging.getLogger(__name__)


# ── Block Builders ───────────────────────────────────────────────────────────────

def build_workspace_blocks(thread_ts: str) -> list:
    """
    Block Kit blocks for workspace selection.
    New Project opens a modal. Existing projects are buttons.
    Also shows "Preferences" button to update anytime.
    """
    existing = list_workspaces(str(WORKSPACE_ROOT))

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "👋 Welcome! Please select or create a workspace to get started:",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "🆕 New Project", "emoji": True},
                    "style":     "primary",
                    "action_id": "workspace_new",
                    "value":     thread_ts,
                }
            ],
        },
    ]

    if existing:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Or continue an existing project:*"},
        })
        elements = []
        for name in existing[:5]:
            elements.append({
                "type":      "button",
                "text":      {"type": "plain_text", "text": f"📁 {name}", "emoji": True},
                "action_id": f"workspace_existing_{name}",
                "value":     f"{thread_ts}|{name}",
            })
        blocks.append({"type": "actions", "elements": elements})

    # Always add "Set Preferences" button so user can update anytime
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*Or update your preferences:*"},
    })
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type":      "button",
                "text":      {"type": "plain_text", "text": "⚙️ Preferences", "emoji": True},
                "action_id": "global_user_prefs",
                "value":     thread_ts,
            }
        ],
    })

    return blocks


def build_new_project_modal(thread_ts: str, trigger_id: str) -> dict:
    """Modal for entering a new project name."""
    return {
        "trigger_id": trigger_id,
        "view": {
            "type":             "modal",
            "callback_id":      "new_project_modal",
            "private_metadata": thread_ts,
            "title":            {"type": "plain_text", "text": "New Project"},
            "submit":           {"type": "plain_text", "text": "Create"},
            "close":            {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type":     "input",
                    "block_id": "project_name_block",
                    "label":    {"type": "plain_text", "text": "Project Name"},
                    "element":  {
                        "type":        "plain_text_input",
                        "action_id":   "project_name_input",
                        "placeholder": {"type": "plain_text", "text": "e.g. my-web-scraper"},
                        "min_length":  2,
                        "max_length":  50,
                    },
                    "hint": {
                        "type": "plain_text",
                        "text": "Use letters, numbers, and hyphens only.",
                    },
                }
            ],
        },
    }


def build_user_preferences_modal(thread_ts: str, trigger_id: str, existing_name: str = "", existing_prefs: str = "") -> dict:
    """
    Modal for collecting user preferences.
    Can be used for initial setup or updating existing preferences.
    """
    # Dynamic text based on whether this is first time or update
    is_update = bool(existing_name)
    title = "⚙️ Update Preferences" if is_update else "👋 Welcome!"
    intro_text = (
        "Update your preferences anytime. Your current preferences will be pre-filled below."
        if is_update
        else "Thanks for trying Gemma Swarm! Let's personalize your experience."
    )
    close_text = "Cancel" if is_update else "Skip"

    return {
        "trigger_id": trigger_id,
        "view": {
            "type":             "modal",
            "callback_id":      "user_preferences_modal",
            "private_metadata": thread_ts,
            "title":            {"type": "plain_text", "text": title},
            "submit":           {"type": "plain_text", "text": "Save"},
            "close":            {"type": "plain_text", "text": close_text},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": intro_text,
                    },
                },
                {"type": "divider"},
                {
                    "type":     "input",
                    "block_id": "user_name_block",
                    "label":    {"type": "plain_text", "text": "What would you like me to call you?"},
                    "element":  {
                        "type":          "plain_text_input",
                        "action_id":     "user_name_input",
                        "placeholder":   {"type": "plain_text", "text": "e.g. Wissam"},
                        "min_length":    1,
                        "max_length":    50,
                        **({"initial_value": existing_name} if existing_name else {}),
                    },
                },
                {
                    "type":     "input",
                    "block_id": "user_preferences_block",
                    "optional": True,
                    "label":    {"type": "plain_text", "text": "Any other preferences?"},
                    "element":  {
                        "type":          "plain_text_input",
                        "action_id":     "user_preferences_input",
                        "multiline":     True,
                        "placeholder":   {"type": "plain_text", "text": "e.g. Be more concise, use formal language..."},
                        "max_length":    200,
                        **({"initial_value": existing_prefs} if existing_prefs else {}),
                    },
                },
            ],
        },
    }


# ── Preferences Helpers ────────────────────────────────────────────────────────

def has_user_preferences() -> bool:
    """Check if global user preferences file exists."""
    return os.path.exists(USER_PREFERENCES_FILE)


def load_user_preferences() -> dict:
    """
    Load global user preferences from JSON file.
    Returns empty dict if file doesn't exist.
    """
    if not os.path.exists(USER_PREFERENCES_FILE):
        return {}
    
    try:
        with open(USER_PREFERENCES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[workspace] Could not load user preferences: {e}")
        return {}


def save_user_preferences(name: str, preferences: str = "") -> dict:
    """
    Save global user preferences to JSON file.
    Returns the saved preferences dict.
    """
    prefs = {
        "name": name.strip() if name else "",
        "preferences": preferences.strip()[:200] if preferences else "",
    }
    
    try:
        with open(USER_PREFERENCES_FILE, 'w', encoding='utf-8') as f:
            json.dump(prefs, f, indent=2, ensure_ascii=False)
        logger.info(f"[workspace] Saved global user preferences")
    except Exception as e:
        logger.error(f"[workspace] Could not save user preferences: {e}")
    
    return prefs


def get_user_preferences_prompt() -> str:
    """
    Get formatted human preferences for injecting into supervisor system prompt.
    Returns empty string if no preferences exist.
    """
    prefs = load_user_preferences()
    if not prefs:
        return ""
    
    parts = []
    
    if prefs.get("name"):
        parts.append(f"- The human's name is: {prefs['name']}")
    
    if prefs.get("preferences"):
        parts.append(f"- Additional preferences: {prefs['preferences']}")
    
    if parts:
        return "\n**Human preferences:**\n" + "\n".join(parts)
    
    return ""


def activate_workspace(thread_ts, channel, workspace_path, project_name, client, run_agent_fn, say, trigger_id=None):
    """
    Called after workspace is selected (new or existing).
    Sets workspace in thread state and runs the pending message if any.
    If trigger_id is provided and global preferences don't exist, opens preferences modal.
    """
    state = get_thread_state(thread_ts)
    state.workspace_path   = workspace_path
    state.project_name     = project_name
    state.active_thread_id = thread_ts
    state.active_channel    = channel
    state.channel_id        = channel
    
    # Set current session for file upload handling
    set_current_session(project_name, channel, thread_ts)

    # Check if this project already has a LangGraph thread with history
    existing_thread = get_project_original_thread(project_name)
    if existing_thread and existing_thread != thread_ts:
        # Reuse the old LangGraph thread — full history preserved
        state.langgraph_thread_ts = existing_thread
        logger.info(f"[workspace] Resuming project '{project_name}' on existing thread {existing_thread}")
    else:
        # New project or same thread — use current thread_ts
        state.langgraph_thread_ts = thread_ts
        # Persist to thread_registry.json
        save_thread_workspace(thread_ts, workspace_path, project_name, channel)

    # Check if global preferences don't exist and we should prompt for them
    if trigger_id and not has_user_preferences():
        # Show preferences setup after workspace confirmation
        try:
            client.chat_update(
                channel=channel,
                ts=state.workspace_msg_ts,
                text=f"📁 Workspace set to *{project_name}*. Setting up your preferences...",
                blocks=[],
            )
            client.views_open(**build_user_preferences_modal(thread_ts, trigger_id))
            # Don't run pending message yet - wait for preferences
            return
        except Exception as e:
            logger.warning(f"[slack] Could not open preferences modal: {e}")

    # Replace workspace selection message with clean confirmation
    if state.workspace_msg_ts:
        try:
            client.chat_update(
                channel=channel,
                ts=state.workspace_msg_ts,
                text=f"📁 Workspace set to *{project_name}*.",
                blocks=[],
            )
        except Exception as e:
            logger.warning(f"[slack] Could not clean up workspace message: {e}")

    # Run pending message
    pending = state.pending_message
    if pending:
        state.pending_message = ""
        state.pending_channel = ""
        state.cancel_event    = threading.Event()
        threading.Thread(
            target=run_agent_fn,
            args=(pending, thread_ts, channel, client, say),
            daemon=True,
        ).start()


# ── Register Handlers ──────────────────────────────────────────────────────────

def register_workspace_handlers(app, run_agent_fn=None):
    """Register workspace and preferences handlers on the Slack Bolt app."""
    
    # Store run_agent function for use in handlers
    _run_agent = run_agent_fn
    
    @app.action("workspace_new")
    def handle_workspace_new(ack, body, client):
        ack()
        trigger_id = body["trigger_id"]
        thread_ts  = body["actions"][0]["value"]
        try:
            client.views_open(**build_new_project_modal(thread_ts, trigger_id))
        except Exception as e:
            logger.error(f"[slack] Could not open modal: {e}")


    @app.view("new_project_modal")
    def handle_new_project_submit(ack, body, client, say):
        ack()
        thread_ts    = body["view"]["private_metadata"]
        trigger_id   = body["trigger_id"]
        values       = body["view"]["state"]["values"]
        project_name = values["project_name_block"]["project_name_input"]["value"].strip()

        if not project_name:
            return

        state   = get_thread_state(thread_ts)
        channel = state.pending_channel

        try:
            workspace_path = create_workspace(str(WORKSPACE_ROOT), project_name)
        except Exception as e:
            logger.error(f"[slack] Could not create workspace: {e}")
            try:
                client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts,
                    text=f"❌ Could not create workspace: {e}",
                )
            except Exception:
                pass
            return

        activate_workspace(
            thread_ts=thread_ts,
            channel=channel,
            workspace_path=workspace_path,
            project_name=project_name,
            client=client,
            run_agent_fn=_run_agent,
            say=say,
            trigger_id=trigger_id,
        )


    @app.action(re.compile(r"workspace_existing_.+"))
    def handle_workspace_existing(ack, body, client, say):
        ack()
        value     = body["actions"][0]["value"]
        parts     = value.split("|", 1)
        thread_ts = parts[0]
        name      = parts[1] if len(parts) > 1 else ""
        channel   = body["channel"]["id"]

        if not name:
            return

        activate_workspace(
            thread_ts=thread_ts,
            channel=channel,
            workspace_path=str(WORKSPACE_ROOT / name),
            project_name=name,
            client=client,
            run_agent_fn=_run_agent,
            say=say,
        )


    @app.action("setup_user_preferences")
    def handle_setup_user_preferences(ack, body, client):
        """Open the user preferences modal after workspace is created."""
        ack()
        trigger_id = body["trigger_id"]
        thread_ts  = body["actions"][0]["value"]
        
        try:
            client.views_open(**build_user_preferences_modal(thread_ts, trigger_id))
        except Exception as e:
            logger.error(f"[slack] Could not open preferences modal: {e}")


    @app.action("global_user_prefs")
    def handle_global_user_preferences(ack, body, client):
        """Open the global user preferences modal from workspace selection."""
        ack()
        trigger_id = body["trigger_id"]
        thread_ts  = body["actions"][0]["value"]
        
        # Load existing preferences to pre-fill the modal
        existing = load_user_preferences()
        existing_name = existing.get("name", "")
        existing_prefs = existing.get("preferences", "")
        
        try:
            client.views_open(**build_user_preferences_modal(thread_ts, trigger_id, existing_name, existing_prefs))
        except Exception as e:
            logger.error(f"[slack] Could not open preferences modal: {e}")


    @app.view("user_preferences_modal")
    def handle_user_preferences_submit(ack, body, client, say):
        """Handle user preferences modal submission."""
        ack()
        thread_ts = body["view"]["private_metadata"]
        
        values = body["view"]["state"]["values"]
        
        # Get user name (required)
        name_block = values.get("user_name_block", {})
        name_input = name_block.get("user_name_input", {})
        user_name = name_input.get("value", "") if name_input else ""
        
        # Get preferences (optional)
        prefs_block = values.get("user_preferences_block", {})
        prefs_input = prefs_block.get("user_preferences_input", {})
        user_prefs = prefs_input.get("value", "") if prefs_input else ""
        
        # Save global preferences
        if user_name:
            save_user_preferences(user_name, user_prefs)
        
        # Notify user that preferences are saved and clean up the message
        state = get_thread_state(thread_ts)
        channel = state.pending_channel or state.active_channel or ""
        
        if channel:
            try:
                # Check if user already has a workspace selected
                has_workspace = bool(state.workspace_path)
                
                # Check if there's a pending message to run
                pending_msg = state.pending_message
                
                # If no workspace OR no pending message, show workspace selection
                # (This handles: Preferences clicked from workspace selection, or first-time setup)
                if not has_workspace or not pending_msg:
                    # Clean up any existing workspace message
                    if state.workspace_msg_ts:
                        client.chat_update(
                            channel=channel,
                            ts=state.workspace_msg_ts,
                            text=f"✅ Preferences saved! Hello{', ' + user_name if user_name else ''}! Please select or create a project:",
                            blocks=build_workspace_blocks(thread_ts),
                        )
                    else:
                        client.chat_postMessage(
                            channel=channel,
                            thread_ts=thread_ts,
                            text=f"✅ Preferences saved! Hello{', ' + user_name if user_name else ''}! Please select or create a project:",
                            blocks=build_workspace_blocks(thread_ts),
                        )
                    return
                
                # Has workspace and has pending message - run the agent
                # (This handles: First-time setup after workspace was already selected)
                # Clean up the workspace selection message
                if state.workspace_msg_ts:
                    client.chat_update(
                        channel=channel,
                        ts=state.workspace_msg_ts,
                        text=f"✅ Preferences saved! Hello{', ' + user_name if user_name else ''}!",
                        blocks=[],
                    )
                
                state.pending_message = ""
                state.pending_channel = ""
                state.cancel_event    = threading.Event()
                threading.Thread(
                    target=getattr(register_workspace_handlers, '_run_agent', None),
                    args=(pending_msg, thread_ts, channel, client, say),
                    daemon=True,
                ).start()
            except Exception as e:
                logger.error(f"[slack] Could not confirm preferences save: {e}")


    @app.view_closed("user_preferences_modal")
    def handle_user_preferences_closed(ack, body, client, say):
        """Handle user closing the preferences modal without saving."""
        ack()
        thread_ts = body["view"]["private_metadata"]
        
        state = get_thread_state(thread_ts)
        channel = state.pending_channel or state.active_channel or ""
        
        if channel:
            try:
                # Clean up the workspace selection message (remove buttons)
                if state.workspace_msg_ts:
                    client.chat_update(
                        channel=channel,
                        ts=state.workspace_msg_ts,
                        text="👋 You can set your preferences anytime! What would you like to do?",
                        blocks=[],
                    )
                else:
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text="👋 No problem! You can always set your preferences later. Feel free to send me a message!",
                    )
            except Exception as e:
                logger.error(f"[slack] Could not send skip message: {e}")


def set_run_agent(run_agent_fn):
    """Set the run_agent function for use in handlers."""
    register_workspace_handlers._run_agent = run_agent_fn
