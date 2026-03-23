"""
Gemma Swarm — Preferences Handlers
==================================
Modal handlers for user preferences.
"""

import logging
import threading

from slack_utils.thread_state import get_thread_state
from slack_utils.handlers_workspace import (
    build_user_preferences_modal,
    build_workspace_blocks,
    save_user_preferences,
    load_user_preferences,
)

logger = logging.getLogger(__name__)


def register_preferences_handlers(app, run_agent_fn=None):
    """Register preferences handlers on the Slack Bolt app."""
    
    _run_agent = run_agent_fn
    
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
                if not has_workspace or not pending_msg:
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
                    target=_run_agent,
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
