"""
Gemma Swarm — Slack Block Builders
==================================
Centralized location for all Slack Block Kit constructions.
"""

from agents_utils.config import HUMAN_CONFIRMATION_TIMEOUT, INTERRUPT_BUTTON_TIMEOUT

def build_interrupt_blocks(thread_ts: str, interrupt_message: str) -> list:
    """Build interrupt decision blocks."""
    preview = interrupt_message[:80] + "..." if len(interrupt_message) > 80 else interrupt_message
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⚡ *New message received while working on a task.*\nNew message: _{preview}_\n\nWhat should I do?",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "🔀 Combine", "emoji": True},
                    "action_id": "interrupt_combine",
                    "value":     f"{thread_ts}|{interrupt_message[:200]}",
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "🆕 Fresh Start", "emoji": True},
                    "style":     "primary",
                    "action_id": "interrupt_fresh",
                    "value":     f"{thread_ts}|{interrupt_message[:200]}",
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "📋 Queue", "emoji": True},
                    "action_id": "interrupt_queue",
                    "value":     f"{thread_ts}|{interrupt_message[:200]}",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_No response in {INTERRUPT_BUTTON_TIMEOUT // 60} minutes → will queue automatically_"}
            ],
        },
    ]


def build_confirmation_blocks(pending_action: str, thread_ts: str) -> list:
    """Standard Approve/Reject buttons for general confirmations."""
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"⚠️ *Human confirmation required.*\n\n{pending_action}"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✅ Approve", "emoji": True},
                    "style":     "primary",
                    "action_id": "confirm_approve",
                    "value":     thread_ts,
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "❌ Reject", "emoji": True},
                    "style":     "danger",
                    "action_id": "confirm_reject",
                    "value":     thread_ts,
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_No response in {HUMAN_CONFIRMATION_TIMEOUT // 60} minutes → defaults to Reject_"}
            ],
        },
    ]


def build_email_preview_blocks(draft: dict, thread_ts: str) -> list:
    """Email preview with Approve and Reject (opens feedback modal) buttons."""
    to_list  = ", ".join(draft.get("to", []))
    subject  = draft.get("subject", "")
    body     = draft.get("rendered_body", draft.get("message", ""))
    language = draft.get("language", "english")
    layout   = draft.get("layout", "official")

    body_preview = body[:2800] + "..." if len(body) > 2800 else body

    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "📧 *Email Draft — Please Review*"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*To:*\n{to_list}"},
                {"type": "mrkdwn", "text": f"*Subject:*\n{subject}"},
                {"type": "mrkdwn", "text": f"*Language:*\n{language}"},
                {"type": "mrkdwn", "text": f"*Layout:*\n{layout}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Message:*\n```{body_preview}```"},
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✅ Send Email", "emoji": True},
                    "style":     "primary",
                    "action_id": "email_approve",
                    "value":     thread_ts,
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✏️ Reject & Give Feedback", "emoji": True},
                    "style":     "danger",
                    "action_id": "email_reject_feedback",
                    "value":     thread_ts,
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_No response in {HUMAN_CONFIRMATION_TIMEOUT // 60} minutes → defaults to Reject_"}
            ],
        },
    ]


def build_feedback_modal(thread_ts: str, title: str = "Email Feedback", callback_id: str = "email_feedback_modal") -> dict:
    """Generic feedback modal for Email, LinkedIn, or Google."""
    return {
        "type":            "modal",
        "callback_id":     callback_id,
        "private_metadata": thread_ts,
        "title":           {"type": "plain_text", "text": title},
        "submit":          {"type": "plain_text", "text": "Submit"},
        "close":           {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type":    "input",
                "block_id": "feedback_block",
                "label":   {"type": "plain_text", "text": "What should be changed?"},
                "element": {
                    "type":        "plain_text_input",
                    "action_id":   "feedback_input",
                    "multiline":   True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "e.g. Make it more formal, shorten the second paragraph, translate to French...",
                    },
                },
            }
        ],
    }


def build_linkedin_preview_blocks(draft: dict, thread_ts: str) -> list:
    """LinkedIn post preview with Approve and Reject (feedback modal) buttons."""
    post_text      = draft.get("post_text", "")
    media_filename = draft.get("media_filename", "")
    language       = draft.get("language", "english")

    preview = post_text[:2800] + "..." if len(post_text) > 2800 else post_text
    media_line = f"\n📎 *Attached:* `{media_filename}`" if media_filename else ""
    lang_line  = f" · Language: {language}" if language != "english" else ""

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*📝 LinkedIn Post — Please Review*{lang_line}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{preview}{media_line}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✅ Approve & Post", "emoji": True},
                    "style":     "primary",
                    "action_id": "linkedin_approve",
                    "value":     thread_ts,
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✏️ Reject & Give Feedback", "emoji": True},
                    "style":     "danger",
                    "action_id": "linkedin_reject_feedback",
                    "value":     thread_ts,
                },
            ],
        },
    ]


def build_google_preview_blocks(result_text: str, thread_ts: str) -> list:
    """Google write action preview with Confirm and Reject (opens feedback modal) buttons."""
    preview = result_text[:10000] + "..." if len(result_text) > 10000 else result_text
 
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "🔵 *Google Action — Please Review*"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": preview},
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✅ Confirm", "emoji": True},
                    "style":     "primary",
                    "action_id": "google_approve",
                    "value":     thread_ts,
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✏️ Reject & Give Feedback", "emoji": True},
                    "style":     "danger",
                    "action_id": "google_reject_feedback",
                    "value":     thread_ts,
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_No response in {HUMAN_CONFIRMATION_TIMEOUT // 60} minutes → defaults to Reject_"}
            ],
        },
    ]
