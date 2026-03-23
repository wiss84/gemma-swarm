"""
Gemma Swarm — Email Composer Agent
=====================================
Composes emails based on user instructions.
Handles: writing, language translation, layout selection.
Sending is handled by email_sender_tool.py after human approval.

Model: gemma-3-2b-it (32k context — sufficient for email tasks)
"""

import json
import logging
import os
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from langchain_core.messages import HumanMessage
from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from agents_utils.config import LABEL, HUMAN_EMAIL, EMAIL_PASSWORD

logger = logging.getLogger(__name__)


# ── Email Sending ──────────────────────────────────────────────────────────────

def send_email(draft: dict, workspace_path: str) -> tuple[bool, str]:
    """
    Send the approved email draft via Gmail SMTP.
    Returns (success, message).
    """
    if not HUMAN_EMAIL or not EMAIL_PASSWORD:
        return False, "Email credentials missing in .env (HUMAN_EMAIL, EMAIL_PASS)."

    recipients  = draft.get("to", [])
    subject     = draft.get("subject", "")
    body        = draft.get("rendered_body", draft.get("message", ""))
    attachments = draft.get("attachments", [])

    if not recipients:
        return False, "No recipients specified."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = HUMAN_EMAIL
    msg["To"]      = ", ".join(recipients)
    msg.set_content(body)

    # Attachments
    allowed_ext = {"pdf", "csv", "png", "jpg", "jpeg", "txt", "docx", "xlsx", "zip", "rar"}
    attach_dir  = Path(workspace_path) / "email_media" / "attachments" if workspace_path else None

    for path_str in attachments:
        # Resolve relative paths against email_attachments folder
        path = Path(path_str)
        if not path.is_absolute() and attach_dir:
            path = attach_dir / path

        if not path.exists():
            return False, f"Attachment not found: {path}"

        ext = path.suffix.lstrip(".").lower()
        if ext not in allowed_ext:
            return False, f"Attachment type '.{ext}' not allowed: {path.name}"

        maintype = "image" if ext in {"png", "jpg", "jpeg"} else "application"
        subtype  = "jpeg" if ext == "jpg" else ext

        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=path.name)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(HUMAN_EMAIL, EMAIL_PASSWORD)
            smtp.send_message(msg)
        logger.info(f"[email_composer] Email sent to {recipients}")
        return True, f"Email sent successfully to {', '.join(recipients)}."
    except smtplib.SMTPAuthenticationError:
        return False, "Authentication failed — check your Gmail App Password in .env."
    except Exception as e:
        return False, f"Failed to send email: {e}"


def save_draft(workspace_path: str, draft: dict):
    """Save email draft to workspace/email_media/drafts/ for record keeping."""
    if not workspace_path:
        return
    drafts_dir = Path(workspace_path) / "email_media" / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath  = drafts_dir / f"{timestamp}_draft.json"
    try:
        filepath.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"[email_composer] Draft saved to {filepath}")
    except OSError as e:
        logger.error(f"[email_composer] Could not save draft: {e}")


def _layout_official(message: str) -> str:
    """Official layout — clean professional formatting."""
    return message.strip()


def _layout_casual(message: str) -> str:
    """Casual layout — message as-is."""
    return message.strip()


def render_layout(draft: dict) -> str:
    """Render email body using the chosen layout."""
    layout_name = draft.get("layout", "casual").lower()
    message     = draft.get("message", "")

    if layout_name == "official":
        return _layout_official(message)
    return _layout_casual(message)


# ── Email Composer Agent ───────────────────────────────────────────────────────

class EmailComposerAgent(BaseAgent):

    def __init__(self):
        super().__init__("email_composer")

    def get_system_prompt(self) -> str:
        from system_prompts.email_composer_prompt import get_prompt
        return get_prompt()

    def compose(self, task: str, messages: list, feedback: str = "", previous_draft: dict | None = None) -> dict | None:
        task_content = f"{LABEL['supervisor']}\n{task}"
        if previous_draft:
            prev_to      = ", ".join(previous_draft.get("to", []))
            prev_subject = previous_draft.get("subject", "")
            prev_preview = previous_draft.get("message", previous_draft.get("rendered_body", ""))
            prev_lang    = previous_draft.get("language", "english")
            prev_layout  = previous_draft.get("layout", "official")
            task_content += (
                "\n\nPrevious draft (for revision):\n"
                f"To: {prev_to}\n"
                f"Subject: {prev_subject}\n"
                f"Language: {prev_lang}\n"
                f"Layout: {prev_layout}\n"
                f"Message: {prev_preview}\n"
            )
        if feedback:
            task_content += f"\n\nFeedback from human review: {feedback}\nPlease rewrite the email incorporating this feedback."

        task_message = HumanMessage(content=task_content)
        response_text, parsed = self.run(messages=messages + [task_message])

        if parsed and isinstance(parsed, dict) and "to" in parsed:
            return parsed

        # Try to extract JSON manually if parsing failed
        match = re.search(r"\{[\s\S]+\}", response_text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        logger.error(f"[email_composer] Could not parse draft from response: {response_text[:200]}")
        return None


# ── LangGraph Node ─────────────────────────────────────────────────────────────

_email_composer_agent = None

def get_email_composer_agent() -> EmailComposerAgent:
    global _email_composer_agent
    if _email_composer_agent is None:
        _email_composer_agent = EmailComposerAgent()
    return _email_composer_agent


def email_composer_node(state: AgentState) -> dict:
    """
    Composes an email draft and stores it in state.
    After this node, graph routes to human_gate for approval.
    If human approves → send. If human rejects with feedback → recompose.
    """
    agent           = get_email_composer_agent()
    current_subtask = state.get("current_subtask", "")
    messages        = state.get("messages", [])
    workspace_path  = state.get("workspace_path", "")
    human_decision  = state.get("human_decision", "")
    email_history   = list(state.get("email_history", []))

    # Get feedback if this is a recompose after rejection
    feedback = ""
    if human_decision and human_decision.startswith("rejected:"):
        feedback = human_decision.replace("rejected:", "").strip()
        logger.info(f"[email_composer] Recomposing with feedback: {feedback[:80]}")

    logger.info(f"[email_composer] Composing email for: {current_subtask[:80]}")

    previous_email_draft = state.get("email_draft", {})
    draft = agent.compose(
        task=current_subtask,
        messages=email_history,
        feedback=feedback,
        previous_draft=previous_email_draft if previous_email_draft else None,
    )

    if not draft:
        return {
            "active_agent":  "email_composer",
            "next_node":     "supervisor",
            "error_message": "Email composer failed to produce a draft.",
            "messages": messages + [
                HumanMessage(content=f"{LABEL['email_composer']}\nFailed to compose email.")
            ],
        }

    # Render layout and save draft
    rendered_body = render_layout(draft)
    draft["rendered_body"] = rendered_body

    if workspace_path:
        save_draft(workspace_path, draft)

    logger.info(f"[email_composer] Draft ready for: {draft.get('to', [])}")

    task_msg   = HumanMessage(content=f"{LABEL['supervisor']}\n{current_subtask}")
    result_msg = HumanMessage(content=f"{LABEL['email_composer']}\nEmail draft ready for review.")
    email_history.extend([task_msg, result_msg])

    return {
        "email_draft":    draft,
        "active_agent":   "email_composer",
        "next_node":      "human_gate",
        "human_decision": "",
        "requires_confirmation": True,
        "email_history":  email_history,
        "messages": messages + [result_msg],
    }


def email_send_node(state: AgentState) -> dict:
    """
    Sends the approved email draft.
    Called after human_gate approves.
    """
    draft          = state.get("email_draft", {})
    workspace_path = state.get("workspace_path", "")
    messages       = state.get("messages", [])

    if not draft:
        return {
            "active_agent": "email_composer",
            "next_node":    "output_formatter",
            "task_complete": True,
            "messages": messages + [
                HumanMessage(content=f"{LABEL['email_composer']}\nNo email draft found to send.")
            ],
        }

    success, result_msg = send_email(draft, workspace_path)

    status = "✅ Email sent successfully." if success else f"❌ Failed to send email: {result_msg}"
    logger.info(f"[email_send] {result_msg}")

    return {
        "active_agent":  "email_send",
        "next_node":     "supervisor",
        "task_complete": True,
        "email_draft":   {},  # Clear draft after sending
        "messages": messages + [
            HumanMessage(content=f"{LABEL['email_composer']}\n{status}")
        ],
    }
