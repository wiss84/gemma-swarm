"""
Gemma Swarm — Toolset Registry
================================
All tools are self-contained: they do their work AND handle any human
confirmation internally, returning a plain string result to the supervisor.

The supervisor only calls tools. Zero routing logic. Zero next_node.
"""

import json
import logging
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from agents_utils.optional_feature import is_feature_enabled

logger = logging.getLogger(__name__)

CONFIG_MISSING_PREFIX = "CONFIG_MISSING:"

_SETUP_DOCS = {
    "google":   "docs/setup/google_setup.md",
    "linkedin": "docs/setup/linkedin_setup.md",
    "email":    "docs/setup/email_setup.md",
}

# Injected by supervisor_agent_node() before think() so blocking tools can use Slack
_slack_client_ref    = None
_slack_thread_ts_ref = None
_slack_channel_ref   = None


def set_slack_context(client, thread_ts: str, channel: str):
    global _slack_client_ref, _slack_thread_ts_ref, _slack_channel_ref
    _slack_client_ref    = client
    _slack_thread_ts_ref = thread_ts
    _slack_channel_ref   = channel


# ── Google write confirmation helper ─────────────────────────────────────────

def _google_write_with_confirm(action_description: str, execute_fn) -> str:
    from slack_utils.blocks import build_google_preview_blocks
    from nodes.human_gate import register_confirmation, get_decision, clear_confirmation
    from agents_utils.config import HUMAN_CONFIRMATION_TIMEOUT

    client    = _slack_client_ref
    thread_ts = _slack_thread_ts_ref
    channel   = _slack_channel_ref

    if not client or not thread_ts or not channel:
        try:
            result = execute_fn()
            return str(result) if result else "Done."
        except Exception as e:
            return f"Error: {e}"

    try:
        blocks = build_google_preview_blocks(action_description, thread_ts)
        client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                text="🔵 Google action ready for review.", blocks=blocks)
    except Exception as e:
        return f"Error: Could not post Google preview to Slack: {e}"

    event = register_confirmation(thread_ts)
    responded = event.wait(timeout=HUMAN_CONFIRMATION_TIMEOUT)
    decision = get_decision(thread_ts) if responded else "rejected"
    if not responded:
        logger.warning("[google_tool] Confirmation timeout — defaulting to reject.")
    clear_confirmation(thread_ts)

    if decision == "approved":
        try:
            result = execute_fn()
            return str(result) if result else "Done."
        except Exception as e:
            return f"Error executing action: {e}"
    else:
        feedback = decision.replace("rejected:", "").strip() if decision.startswith("rejected:") else ""
        return f"rejected: {feedback}" if feedback else "rejected: User rejected the action."


# ── Research ──────────────────────────────────────────────────────────────────

def _get_research_tools():
    from tools.web_search_tool import search_web, fetch_page, fetch_next_chunk
    return [search_web, fetch_page, fetch_next_chunk]


# ── Gmail ─────────────────────────────────────────────────────────────────────

def _get_gmail_tools():
    from tools.gmail_api import gmail_list_messages, gmail_read_message, gmail_check_for_sender
    return [
        StructuredTool.from_function(func=gmail_list_messages, name="gmail_list_messages",
            description="List Gmail inbox messages. Args: max_results (int, default 5), query (str, optional)."),
        StructuredTool.from_function(func=gmail_read_message, name="gmail_read_message",
            description="Read full content of a Gmail message. Args: message_id (str)."),
        StructuredTool.from_function(func=gmail_check_for_sender, name="gmail_check_for_sender",
            description="Check for emails from a sender. Args: sender_email (str), max_results (int, default 3)."),
    ]


# ── Calendar ──────────────────────────────────────────────────────────────────

def _get_calendar_tools():
    from tools.calendar_api import calendar_create, calendar_delete, calendar_list, calendar_next

    class CalCreateInput(BaseModel):
        summary:     str       = Field(description="Event title")
        start_time:  str       = Field(description="Start time ISO8601")
        end_time:    str       = Field(description="End time ISO8601")
        description: str       = Field(default="")
        attendees:   list[str] = Field(default=[])

    class CalDeleteInput(BaseModel):
        event_id: str = Field(description="Calendar event ID")

    def _cal_create(summary: str, start_time: str, end_time: str, description: str = "", attendees: list = []) -> str:
        return _google_write_with_confirm(
            f"Create calendar event: *{summary}*\nStart: {start_time} → End: {end_time}",
            lambda: calendar_create(summary=summary, start_time=start_time, end_time=end_time,
                                    description=description, attendees=attendees))

    def _cal_delete(event_id: str) -> str:
        return _google_write_with_confirm(
            f"Delete calendar event ID: `{event_id}`",
            lambda: calendar_delete(event_id))

    return [
        StructuredTool.from_function(func=_cal_create, name="calendar_create", args_schema=CalCreateInput,
            description="Create a Google Calendar event. Shows preview to user for confirmation first."),
        StructuredTool.from_function(func=_cal_delete, name="calendar_delete", args_schema=CalDeleteInput,
            description="Delete a Google Calendar event. Requires user confirmation."),
        StructuredTool.from_function(func=calendar_list, name="calendar_list",
            description="List upcoming calendar events. Args: max_results (int), time_min (ISO8601, optional)."),
        StructuredTool.from_function(func=calendar_next, name="calendar_next",
            description="Get the next upcoming calendar event. No args."),
    ]


# ── Docs ──────────────────────────────────────────────────────────────────────

def _get_docs_tools():
    from tools.docs_api import docs_create, docs_update, docs_read

    class DocsCreateInput(BaseModel):
        title:   str = Field(description="Document title")
        content: str = Field(description="Document content")

    class DocsUpdateInput(BaseModel):
        doc_id:  str  = Field(description="Google Doc ID")
        content: str  = Field(description="Content to write or append")
        append:  bool = Field(default=False)

    def _docs_create(title: str, content: str) -> str:
        preview = content[:500] + ("..." if len(content) > 500 else "")
        return _google_write_with_confirm(
            f"Create Google Doc: *{title}*\n\nPreview:\n```{preview}```",
            lambda: docs_create(title=title, content=content))

    def _docs_update(doc_id: str, content: str, append: bool = False) -> str:
        action = "Append to" if append else "Update"
        preview = content[:500] + ("..." if len(content) > 500 else "")
        return _google_write_with_confirm(
            f"{action} Google Doc `{doc_id}`\n\nPreview:\n```{preview}```",
            lambda: docs_update(doc_id=doc_id, content=content, append=append))

    return [
        StructuredTool.from_function(func=_docs_create, name="docs_create", args_schema=DocsCreateInput,
            description="Create a new Google Doc. Shows preview for user confirmation."),
        StructuredTool.from_function(func=_docs_update, name="docs_update", args_schema=DocsUpdateInput,
            description="Update or append to a Google Doc. Requires user confirmation."),
        StructuredTool.from_function(func=docs_read, name="docs_read",
            description="Read content of a Google Doc. Args: doc_id (str)."),
    ]


# ── Sheets ────────────────────────────────────────────────────────────────────

def _get_sheets_tools():
    from tools.sheets_api import sheets_create, sheets_update, sheets_read

    class SheetsCreateInput(BaseModel):
        title: str  = Field(description="Spreadsheet title")
        data:  list = Field(default=[], description="Optional initial rows as list of lists")

    class SheetsUpdateInput(BaseModel):
        spreadsheet_id: str  = Field(description="Spreadsheet ID")
        range:          str  = Field(description="Range in A1 notation e.g. 'Sheet1!A1:C3'")
        values:         list = Field(description="Data rows as list of lists")

    def _sheets_create(title: str, data: list = []) -> str:
        preview = f"{len(data)} rows" if data else "empty"
        return _google_write_with_confirm(
            f"Create Google Sheet: *{title}* ({preview})",
            lambda: sheets_create(title=title, data=data))

    def _sheets_update(spreadsheet_id: str, range: str, values: list) -> str:
        return _google_write_with_confirm(
            f"Update Sheet `{spreadsheet_id}` range `{range}` with {len(values)} row(s)",
            lambda: sheets_update(spreadsheet_id=spreadsheet_id, range=range, values=values))

    return [
        StructuredTool.from_function(func=_sheets_create, name="sheets_create", args_schema=SheetsCreateInput,
            description="Create a new Google Sheet. Shows preview for user confirmation."),
        StructuredTool.from_function(func=_sheets_update, name="sheets_update", args_schema=SheetsUpdateInput,
            description="Update cells in a Google Sheet. Requires user confirmation."),
        StructuredTool.from_function(func=sheets_read, name="sheets_read",
            description="Read data from a Google Sheet. Args: spreadsheet_id (str), range (str, optional)."),
    ]


# ── Email (blocking) ──────────────────────────────────────────────────────────

def _get_email_tools():

    class SendEmailInput(BaseModel):
        to:          list[str] = Field(description="Recipient email addresses")
        subject:     str       = Field(description="Subject line")
        message:     str       = Field(description="Email body (plain text)")
        layout:      str       = Field(default="official", description="'official' or 'casual'")
        language:    str       = Field(default="english")
        attachments: list[str] = Field(default=[])

    def send_email_tool(to: list, subject: str, message: str,
                        layout: str = "official", language: str = "english",
                        attachments: list = []) -> str:
        from tools.email_send import send_email, render_layout
        from slack_utils.blocks import build_email_preview_blocks
        from nodes.human_gate import register_confirmation, get_decision, clear_confirmation
        from agents_utils.config import HUMAN_CONFIRMATION_TIMEOUT

        draft = {"to": to, "subject": subject, "message": message,
                 "layout": layout, "language": language, "attachments": attachments}
        draft["rendered_body"] = render_layout(draft)

        client    = _slack_client_ref
        thread_ts = _slack_thread_ts_ref
        channel   = _slack_channel_ref

        if not client or not thread_ts or not channel:
            success, msg = send_email(draft, workspace_path="")
            return msg

        try:
            blocks = build_email_preview_blocks(draft, thread_ts)
            client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                    text="📧 Email draft ready for review.", blocks=blocks)
        except Exception as e:
            return f"Error: Could not post email preview: {e}"

        event = register_confirmation(thread_ts)
        responded = event.wait(timeout=HUMAN_CONFIRMATION_TIMEOUT)
        decision = get_decision(thread_ts) if responded else "rejected"
        if not responded:
            logger.warning("[email_tool] Timeout — defaulting to reject.")
        clear_confirmation(thread_ts)

        if decision == "approved":
            success, result_msg = send_email(draft, workspace_path="")
            return f"Email sent successfully to {', '.join(to)}." if success else f"Failed to send email: {result_msg}"
        else:
            feedback = decision.replace("rejected:", "").strip() if decision.startswith("rejected:") else ""
            return f"rejected: {feedback}" if feedback else "rejected: User rejected the email draft."

    return [
        StructuredTool.from_function(
            func=send_email_tool, name="send_email", args_schema=SendEmailInput,
            description=(
                "Compose and send an email. Shows the draft to the user for approval. "
                "Returns success message, or 'rejected: <feedback>' if user wants changes — "
                "in that case rewrite the email incorporating the feedback and call this tool again. "
                "Args: to (list), subject (str), message (str), layout ('official'|'casual'), "
                "language (str), attachments (list of paths)."
            ),
        )
    ]


# ── LinkedIn (blocking) ───────────────────────────────────────────────────────

def _get_linkedin_tools():

    class PublishPostInput(BaseModel):
        post_text:      str = Field(description="Full LinkedIn post text")
        language:       str = Field(default="english")
        media_filename: str = Field(default="", description="Optional filename in linkedin_media/post_attachments/")

    def publish_linkedin_post_tool(post_text: str, language: str = "english",
                                   media_filename: str = "") -> str:
        from tools.linkedin_api import publish_linkedin_post, check_rate_limit
        from slack_utils.blocks import build_linkedin_preview_blocks
        from nodes.human_gate import register_confirmation, get_decision, clear_confirmation
        from agents_utils.config import HUMAN_CONFIRMATION_TIMEOUT

        can_post, rate_msg = check_rate_limit()
        if not can_post:
            return f"LinkedIn rate limit reached: {rate_msg}"

        draft = {"post_text": post_text, "media_filename": media_filename,
                 "media_path": "", "language": language}

        client    = _slack_client_ref
        thread_ts = _slack_thread_ts_ref
        channel   = _slack_channel_ref

        if not client or not thread_ts or not channel:
            success, msg = publish_linkedin_post(text=post_text, media_path=None)
            return msg

        try:
            blocks = build_linkedin_preview_blocks(draft, thread_ts)
            client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                    text="📝 LinkedIn post ready for review.", blocks=blocks)
        except Exception as e:
            return f"Error: Could not post LinkedIn preview: {e}"

        event = register_confirmation(thread_ts)
        responded = event.wait(timeout=HUMAN_CONFIRMATION_TIMEOUT)
        decision = get_decision(thread_ts) if responded else "rejected"
        if not responded:
            logger.warning("[linkedin_tool] Timeout — defaulting to reject.")
        clear_confirmation(thread_ts)

        if decision == "approved":
            def slack_post_fn(msg: str):
                try:
                    if client:
                        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=msg)
                except Exception:
                    pass

            success, result_msg = publish_linkedin_post(text=post_text,
                                                         media_path=draft.get("media_path") or None,
                                                         slack_post_fn=slack_post_fn)
            return result_msg
        else:
            feedback = decision.replace("rejected:", "").strip() if decision.startswith("rejected:") else ""
            return f"rejected: {feedback}" if feedback else "rejected: User rejected the LinkedIn post."

    return [
        StructuredTool.from_function(
            func=publish_linkedin_post_tool, name="publish_linkedin_post",
            args_schema=PublishPostInput,
            description=(
                "Write and publish a LinkedIn post. Shows the post to the user for approval. "
                "Returns success message, or 'rejected: <feedback>' if user wants changes — "
                "rewrite the post incorporating the feedback and call this tool again. "
                "Args: post_text (str), language (str), media_filename (str, optional)."
            ),
        )
    ]


# ── Registry ──────────────────────────────────────────────────────────────────

TOOLSETS: dict[str, dict] = {
    "research": {"feature": None,       "get_tools": _get_research_tools, "description": "Web search and page fetching."},
    "gmail":    {"feature": "google",   "get_tools": _get_gmail_tools,    "description": "Read Gmail inbox and setup email watch."},
    "calendar": {"feature": "google",   "get_tools": _get_calendar_tools, "description": "Google Calendar: list, create (confirm), delete (confirm), next event."},
    "docs":     {"feature": "google",   "get_tools": _get_docs_tools,     "description": "Google Docs: create (confirm), update (confirm), read."},
    "sheets":   {"feature": "google",   "get_tools": _get_sheets_tools,   "description": "Google Sheets: create (confirm), update (confirm), read."},
    "email":    {"feature": "email",    "get_tools": _get_email_tools,    "description": "Send emails with user approval. Handles feedback loop automatically."},
    "linkedin": {"feature": "linkedin", "get_tools": _get_linkedin_tools, "description": "Publish LinkedIn posts with user approval. Handles feedback loop automatically."},
}


def load_toolset(toolset_name: str) -> str:
    if toolset_name not in TOOLSETS:
        return f"ERROR: Unknown toolset '{toolset_name}'. Available: {', '.join(TOOLSETS)}"
    entry   = TOOLSETS[toolset_name]
    feature = entry.get("feature")
    if feature and not is_feature_enabled(feature):
        return f"{CONFIG_MISSING_PREFIX}{feature}"
    try:
        tools = entry["get_tools"]()
    except Exception as e:
        logger.error(f"[toolset_registry] Failed to load '{toolset_name}': {e}")
        return f"ERROR: Failed to load toolset '{toolset_name}': {e}"
    tool_info = [{"name": t.name, "description": t.description} for t in tools]
    logger.info(f"[toolset_registry] Loaded '{toolset_name}': {[t['name'] for t in tool_info]}")
    return json.dumps(tool_info)


def get_toolset_tools(toolset_name: str) -> list:
    if toolset_name not in TOOLSETS:
        return []
    entry   = TOOLSETS[toolset_name]
    feature = entry.get("feature")
    if feature and not is_feature_enabled(feature):
        return []
    try:
        return entry["get_tools"]()
    except Exception as e:
        logger.error(f"[toolset_registry] get_toolset_tools('{toolset_name}') failed: {e}")
        return []


def get_available_toolsets_description() -> str:
    lines = []
    for name, entry in TOOLSETS.items():
        feature = entry.get("feature")
        desc    = entry["description"]
        suffix  = " [NOT CONFIGURED]" if (feature and not is_feature_enabled(feature)) else ""
        lines.append(f"  - {name}: {desc}{suffix}")
    return "\n".join(lines)


def build_setup_required_response(feature: str) -> list:
    import os
    from pathlib import Path

    display  = {"google": "Google Workspace", "linkedin": "LinkedIn", "email": "Email"}.get(feature, feature.title())
    rel_path = _SETUP_DOCS.get(feature, f"docs/setup/{feature}_setup.md")
    filename = Path(rel_path).name

    # Build absolute path for the editor deep-link
    project_root = Path(__file__).resolve().parents[1]
    abs_path     = str(project_root / rel_path).replace("\\", "/")
    vscode_url   = f"vscode://file/{abs_path}"

    text_block = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"⚠️ *{display} Setup Required*\n\n"
                f"It looks like you're trying to use the *{display}* feature, but you have not configured yet.\n\n"
                f"Please click the displayed *button* to see how to set it up, or continue the conversation without this feature"
            ),
        },
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": f"📄 {filename}", "emoji": True},
            "url":  vscode_url,
            "action_id": "open_setup_doc",
        },
    }
    return [text_block]
