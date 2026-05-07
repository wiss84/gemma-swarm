"""
Gemma Swarm — Email Send
"""

import json
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from agents_utils.config import HUMAN_EMAIL, EMAIL_PASSWORD

logger = logging.getLogger(__name__)


def send_email(draft: dict, workspace_path: str = "") -> tuple[bool, str]:
    """Send an approved email draft via Gmail SMTP. Returns (success, message)."""
    if not HUMAN_EMAIL or not EMAIL_PASSWORD:
        return False, "Email credentials missing in .env (HUMAN_EMAIL, EMAIL_PASS)."

    recipients  = draft.get("to", [])
    subject     = draft.get("subject", "")
    body        = draft.get("rendered_body", draft.get("message", ""))
    attachments = draft.get("attachments", [])

    if not recipients:
        return False, "No recipients specified."

    msg            = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = HUMAN_EMAIL
    msg["To"]      = ", ".join(recipients)
    msg.set_content(body)

    allowed_ext = {"pdf", "csv", "png", "jpg", "jpeg", "txt", "docx", "xlsx", "zip", "rar"}
    attach_dir  = Path(workspace_path) / "email_media" / "attachments" if workspace_path else None

    for path_str in attachments:
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
        logger.info(f"[email_send] Sent to {recipients}")
        return True, f"Email sent successfully to {', '.join(recipients)}."
    except smtplib.SMTPAuthenticationError:
        return False, "Authentication failed — check your Gmail App Password in .env."
    except Exception as e:
        return False, f"Failed to send email: {e}"


def render_layout(draft: dict) -> str:
    """Render email body using the chosen layout (official or casual)."""
    message = draft.get("message", "")
    return message.strip()


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
    except OSError as e:
        logger.error(f"[email_send] Could not save draft: {e}")
