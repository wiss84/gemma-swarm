"""
Gemma Swarm — File Upload Handlers
===================================
Handles file_shared events and attachment button callbacks.
Manages file downloads and storage to appropriate directories.
"""

import os
import re
import logging
import requests
from pathlib import Path

from slack_utils.thread_state import get_thread_state, get_current_session
from agents_utils.config import WORKSPACE_ROOT, SLACK_BOT_TOKEN

logger = logging.getLogger(__name__)


# ── Attachment Path Helpers ─────────────────────────────────────────────────────

def get_email_attachments_dir(project_name: str):
    """Get email attachments directory for a project."""
    return WORKSPACE_ROOT / project_name / "email_media" / "attachments"

def get_linkedin_attachments_dir(project_name: str):
    """Get LinkedIn post attachments directory for a project."""
    return WORKSPACE_ROOT / project_name / "linkedin_media" / "post_attachments"

def get_context_attachments_dir(project_name: str):
    """Get context attachments directory (src) for a project."""
    return WORKSPACE_ROOT / project_name / "src"


def register_file_handlers(app, run_agent_fn=None):
    """Register file upload handlers on the Slack Bolt app."""
    
    # Store pending file info for button callbacks
    _pending_files = {}
    
    # Store run_agent function for use in handlers
    _run_agent = run_agent_fn
    
    def _get_user_message_from_thread(client, channel_id, thread_ts):
        """Fetch the latest user message from the thread (the message sent with the file)."""
        try:
            result = client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=5  # Get last 5 messages
            )
            messages = result.get("messages", [])
            # Find the most recent message with text (not from bot)
            for msg in reversed(messages):
                if msg.get("text") and not msg.get("bot_id"):
                    return msg.get("text", "")
            return ""
        except Exception as e:
            logger.warning(f"[files] Could not fetch thread messages: {e}")
            return ""
    
    def _preprocess_text_file(file_path: Path) -> str:
        """Basic text preprocessing - extract text content from text files."""
        try:
            # Try to read as UTF-8 text
            content = file_path.read_text(encoding="utf-8")
            # Truncate if too long (limit to ~50KB for context)
            if len(content) > 50000:
                content = content[:50000] + "\n... [truncated]"
            return content
        except Exception as e:
            logger.warning(f"[files] Could not read text file: {e}")
            return "[Could not extract text content]"
    
    @app.event("file_shared")
    def handle_file_shared(event, client):
        """Handle file_shared event when a user uploads a file."""
        # event["file"] only has id — fetch full info from API
        file_id = event["file"]["id"]
        try:
            result    = client.files_info(file=file_id)
            file_info = result["file"]
            file_name = file_info.get("name", "unknown")
            file_url  = file_info.get("url_private", "")
            logger.info(f"[files] File info fetched: {file_name} ({file_id})")
        except Exception as e:
            logger.error(f"[files] Could not fetch file info: {e}")
            return
        
        # Get channel info from event
        channel_id = event.get("channel_id", "")
        thread_ts = event.get("thread_ts", event.get("ts", ""))
        
        # Use current session variables if available and channel matches
        if not thread_ts and channel_id:
            current_project, current_channel, current_thread = get_current_session()
            
            # Use current session if in the same channel
            if current_project and current_channel and current_thread:
                if current_channel == channel_id:
                    thread_ts = current_thread
                    logger.info(f"[files] Using current session: {thread_ts} ({current_project})")
                else:
                    logger.warning(f"[files] File uploaded in different channel ({channel_id}) than current session ({current_channel}). Please mention the bot and select workspace first.")
                    return
            else:
                # Fallback to registry matching if no current session
                from slack_utils.thread_state import get_threads_registry
                registry = get_threads_registry()
                
                # First, try to find workspace for this specific channel
                for ts, state in registry.items():
                    if (state.workspace_path and state.project_name 
                        and getattr(state, 'channel_id', '') == channel_id):
                        thread_ts = ts
                        logger.info(f"[files] Found workspace for channel {channel_id}: {thread_ts} ({state.project_name})")
                        break
                
                # If no match for channel, show warning
                if not thread_ts:
                    logger.warning(f"[files] No workspace found for channel {channel_id}. Please mention the bot and select workspace first.")
                    return
        
        # Check if workspace is already selected for this thread
        state = get_thread_state(thread_ts)
        
        # Get user's message from thread (the message sent with the file)
        user_message = _get_user_message_from_thread(client, channel_id, thread_ts)
        logger.info(f"[files] User message in thread: {user_message[:100] if user_message else '(none)'}")
        
        # Store file info for button callbacks
        file_key = f"{thread_ts}:{file_id}"
        _pending_files[file_key] = {
            "file_id": file_id,
            "file_name": file_name,
            "file_url": file_url,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "user_message": user_message,  # Store user's message
        }
        
        # Build buttons based on workspace selection
        if state.workspace_path and state.project_name:
            # Workspace selected - show attachment buttons
            blocks = _build_attachment_buttons_block(file_id, file_name, thread_ts)
            text = f"📎 File uploaded: *{file_name}*\n\n_{user_message if user_message else 'No message'}_"
        else:
            # No workspace - prompt to select one first
            blocks = _build_workspace_needed_block(file_id, file_name, thread_ts)
            text = f"📎 File uploaded: *{file_name}*\n\nPlease select a workspace first to choose attachment type."
        
        try:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=text,
                blocks=blocks,
            )
        except Exception as e:
            logger.error(f"[files] Could not post file message: {e}")
    
    def _build_attachment_buttons_block(file_id: str, file_name: str, thread_ts: str):
        """Build blocks with attachment type buttons."""
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"📎 *{file_name}*\n\nHow would you like to attach this file?"
                }
            },
            {
                "type": "actions",
                "block_id": f"file_attach_{thread_ts}",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "📧 Email Attachment"
                        },
                        "style": "primary",
                        "action_id": "file_email_attachment",
                        "value": f"{thread_ts}|{file_id}"
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "💼 LinkedIn Attachment"
                        },
                        "style": "primary",
                        "action_id": "file_linkedin_attachment",
                        "value": f"{thread_ts}|{file_id}"
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "📄 Context Attachment"
                        },
                        "action_id": "file_context_attachment",
                        "value": f"{thread_ts}|{file_id}"
                    }
                ]
            }
        ]
    
    def _build_workspace_needed_block(file_id: str, file_name: str, thread_ts: str):
        """Build blocks prompting user to select workspace first."""
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"📎 *{file_name}*\n\n⚠️ Please select a workspace first to choose attachment type."
                }
            }
        ]
    
    # ── Button Handlers ─────────────────────────────────────────────────────────
    
    @app.action("file_email_attachment")
    def handle_email_attachment(ack, body, client, say):
        """Handle email attachment button click - save file and run agent."""
        ack()

        # Delete the attachment buttons message
        try:
            client.chat_delete(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
            )
        except Exception:
            pass
        
        value = body["actions"][0]["value"]
        thread_ts, file_id = value.split("|", 1)
        channel_id = body["channel"]["id"]
        
        file_key = f"{thread_ts}:{file_id}"
        if file_key not in _pending_files:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="⚠️ File information not found. Please upload the file again."
            )
            return
        
        file_data = _pending_files.pop(file_key)
        file_name = file_data["file_name"]
        user_message = file_data.get("user_message", "")
        
        state = get_thread_state(thread_ts)
        project_name = state.project_name
        
        if not project_name:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="⚠️ No workspace selected. Please select a workspace first."
            )
            return
        
        # Download and save file
        target_dir = get_email_attachments_dir(project_name)
        saved_path = _download_and_save_file(client, file_id, file_name, target_dir)
        
        if saved_path:
            # Store only the filename (not full path) in thread state
            from pathlib import Path
            filename_only = Path(file_name).name
            state.email_attachment_path = filename_only
            
            # Build message for agent
            if user_message:
                agent_message = f"Email attachment: {filename_only}\n\nUser request: {user_message}"
            else:
                agent_message = f"Email attachment: {filename_only}\n\nPlease compose an email with this attachment."
            
            # Notify user
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"✅ File saved as *Email Attachment*: `{filename_only}`. Processing..."
            )
            
            # Run agent with the email task
            if _run_agent:
                import threading
                state.cancel_event = threading.Event()
                threading.Thread(
                    target=_run_agent,
                    args=(agent_message, thread_ts, channel_id, client, say),
                    daemon=True,
                ).start()
            else:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=f"⚠️ Agent runner not available. File saved: {filename_only}"
                )
        else:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"❌ Failed to save email attachment: {file_name}"
            )
    
    @app.action("file_linkedin_attachment")
    def handle_linkedin_attachment(ack, body, client, say):
        """Handle LinkedIn attachment button click - save file and run agent."""
        ack()

        # Delete the attachment buttons message
        try:
            client.chat_delete(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
            )
        except Exception:
            pass
        
        value = body["actions"][0]["value"]
        thread_ts, file_id = value.split("|", 1)
        channel_id = body["channel"]["id"]
        
        file_key = f"{thread_ts}:{file_id}"
        if file_key not in _pending_files:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="⚠️ File information not found. Please upload the file again."
            )
            return
        
        file_data = _pending_files.pop(file_key)
        file_name = file_data["file_name"]
        user_message = file_data.get("user_message", "")
        
        state = get_thread_state(thread_ts)
        project_name = state.project_name
        
        if not project_name:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="⚠️ No workspace selected. Please select a workspace first."
            )
            return
        
        # Download and save file
        target_dir = get_linkedin_attachments_dir(project_name)
        saved_path = _download_and_save_file(client, file_id, file_name, target_dir)
        
        if saved_path:
            # Store only the filename (not full path) in thread state
            from pathlib import Path
            filename_only = Path(file_name).name
            state.linkedin_attachment_path = filename_only
            
            # Build message for agent
            if user_message:
                agent_message = f"LinkedIn attachment: {filename_only}\n\nUser request: {user_message}"
            else:
                agent_message = f"LinkedIn attachment: {filename_only}\n\nPlease compose a LinkedIn post with this attachment."
            
            # Notify user
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"✅ File saved as *LinkedIn Attachment*: `{filename_only}`. Processing..."
            )
            
            # Run agent with the LinkedIn task
            if _run_agent:
                import threading
                state.cancel_event = threading.Event()
                threading.Thread(
                    target=_run_agent,
                    args=(agent_message, thread_ts, channel_id, client, say),
                    daemon=True,
                ).start()
            else:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=f"⚠️ Agent runner not available. File saved: {filename_only}"
                )
        else:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"❌ Failed to save LinkedIn attachment: {file_name}"
            )
    
    @app.action("file_context_attachment")
    def handle_context_attachment(ack, body, client, say):
        """Handle context attachment button click - preprocess and run agent."""
        ack()

        # Delete the attachment buttons message
        try:
            client.chat_delete(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
            )
        except Exception:
            pass
        
        value = body["actions"][0]["value"]
        thread_ts, file_id = value.split("|", 1)
        channel_id = body["channel"]["id"]
        
        file_key = f"{thread_ts}:{file_id}"
        if file_key not in _pending_files:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="⚠️ File information not found. Please upload the file again."
            )
            return
        
        file_data = _pending_files.pop(file_key)
        file_name = file_data["file_name"]
        user_message = file_data.get("user_message", "")
        
        state = get_thread_state(thread_ts)
        project_name = state.project_name
        
        if not project_name:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="⚠️ No workspace selected. Please select a workspace first."
            )
            return
        
        # Download and save file
        target_dir = get_context_attachments_dir(project_name)
        saved_path = _download_and_save_file(client, file_id, file_name, target_dir)
        
        if saved_path:
            # Preprocess file content (extract text for text files)
            file_content = _preprocess_text_file(saved_path)
            
            # Build the message for the agent
            if user_message:
                agent_message = f"File: {file_name}\nContent:\n{file_content}\n\nUser request: {user_message}"
            else:
                agent_message = f"File: {file_name}\nContent:\n{file_content}"
            
            # Store file path in thread state
            state.context_attachment_path = str(saved_path)
            
            # Notify user we're processing
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"✅ Processing file: *{file_name}*..."
            )
            
            # Run agent with the file content and user message
            if _run_agent:
                import threading
                state.cancel_event = threading.Event()
                threading.Thread(
                    target=_run_agent,
                    args=(agent_message, thread_ts, channel_id, client, say),
                    daemon=True,
                ).start()
            else:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=f"⚠️ Agent runner not available. File saved to: {saved_path}"
                )
        else:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"❌ Failed to save context attachment: {file_name}"
            )


def _download_and_save_file(client, file_id: str, file_name: str, target_dir: Path) -> Path | None:
    """
    Download a file from Slack and save to target directory.
    Returns the saved file path or None on failure.
    """
    try:
        # Get file info including download URL
        result = client.files_info(file=file_id)
        file_data = result["file"]
        
        # Get download URL (prefer file_slack_dl_url, fallback to url_private)
        download_url = file_data.get("file_slack_dl_url") or file_data.get("url_private")
        
        if not download_url:
            logger.error("[files] No download URL found in file info")
            return None
        
        # Create target directory
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Sanitize filename
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', file_name)
        target_path = target_dir / safe_name
        
        # Download file content using bot token
        headers = {
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}"
        }
        
        response = requests.get(download_url, headers=headers, timeout=60)
        
        if response.status_code != 200:
            logger.error(f"[files] Download failed: {response.status_code}")
            return None
        
        # Save to disk
        target_path.write_bytes(response.content)
        
        logger.info(f"[files] Saved file: {target_path}")
        return target_path
        
    except Exception as e:
        logger.error(f"[files] Error downloading file: {e}")
        return None
