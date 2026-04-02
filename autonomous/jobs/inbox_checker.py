"""
Gemma Swarm — Autonomous Inbox Checker
========================================
Polls Gmail inbox for ALL unread emails periodically.
Only notifies about emails not seen in the previous check.
Separate from email_watcher which tracks specific senders.
Zero LLM calls.
"""

import logging

logger = logging.getLogger(__name__)


def run(slack_client, autonomous_channel_id: str):
    """
    Fetch all unread emails from inbox.
    Compare against last_seen_ids from previous run.
    Post Slack notification only for genuinely new emails.
    Update last_seen_ids after each run.
    """
    from autonomous.settings import load_settings, save_settings
    from autonomous.jobs.activity_logger import log
    from tools.google_api import _get_access_token, _auth_headers
    import requests

    settings      = load_settings()
    prev_seen_ids = set(settings["inbox_check"].get("last_seen_ids", []))

    try:
        token  = _get_access_token()
        params = {
            "maxResults": 20,
            "q":          "is:unread",
            "labelIds":   "INBOX",
        }
        response = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers=_auth_headers(token),
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        messages = response.json().get("messages", [])

        if not messages:
            logger.info("[inbox_checker] No unread emails in inbox.")
            settings["inbox_check"]["last_seen_ids"] = []
            save_settings(settings)
            return

        current_ids = {m["id"] for m in messages}
        new_ids     = current_ids - prev_seen_ids

        if not new_ids:
            logger.info("[inbox_checker] No new unread emails since last check.")
            return

        # Fetch metadata for new emails only
        new_emails = []
        for msg in messages:
            if msg["id"] not in new_ids:
                continue

            detail = requests.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}",
                headers=_auth_headers(token),
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                timeout=15,
            )
            detail.raise_for_status()
            data    = detail.json()
            headers = {
                h["name"].lower(): h["value"]
                for h in data.get("payload", {}).get("headers", [])
            }
            new_emails.append({
                "from":    headers.get("from", "Unknown"),
                "subject": headers.get("subject", "(no subject)"),
                "date":    headers.get("date", ""),
            })

        if not new_emails:
            return

        # Build Slack notification
        lines = [f"📬 *{len(new_emails)} new unread email(s) in your inbox:*\n"]
        for e in new_emails:
            lines.append(f"• *From:* {e['from']} | *Subject:* {e['subject']}")

        notify_text = "\n".join(lines)

        try:
            slack_client.chat_postMessage(
                channel=autonomous_channel_id,
                text=notify_text,
                mrkdwn=True,
            )
            logger.info(f"[inbox_checker] Notified {len(new_emails)} new emails.")
            log("inbox_checker", f"{len(new_emails)} new unread email(s) found", "✅")
        except Exception as e:
            logger.error(f"[inbox_checker] Slack post failed: {e}")
            log("inbox_checker", "Failed to post inbox notification", "❌")

        # Update last_seen_ids to current full set
        settings["inbox_check"]["last_seen_ids"] = list(current_ids)[:50]
        save_settings(settings)

    except Exception as e:
        logger.error(f"[inbox_checker] Error: {e}")
        log("inbox_checker", f"Inbox check failed: {e}", "❌")
