"""
Gemma Swarm — Autonomous Inbox Checker
========================================
Polls Gmail inbox for ALL unread emails periodically.
Only notifies about emails not seen in the previous check.
Emails from watched senders (email_watch.senders) are excluded —
those are already handled with richer notifications by email_watcher.py.
Zero LLM calls.
"""

import logging

logger = logging.getLogger(__name__)


def run(slack_client, autonomous_channel_id: str):
    """
    Fetch all unread emails from inbox.
    Compare against last_seen_ids from previous run.
    Post Slack notification only for genuinely new emails.
    Skips emails from senders already covered by email_watcher.
    Update last_seen_ids after each run.
    """
    from autonomous.settings import load_settings, save_settings
    from autonomous.jobs.activity_logger import log
    from tools.google_api import _get_access_token, _auth_headers
    import requests

    settings      = load_settings()
    prev_seen_ids = set(settings["inbox_check"].get("last_seen_ids", []))

    # Build a set of watched sender addresses (lowercased) to exclude from inbox notifications
    watched_senders = {
        s.strip().lower()
        for s in settings["email_watch"].get("senders", [])
        if s.strip()
    }

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
            # Still update last_seen_ids in case emails were read externally
            settings["inbox_check"]["last_seen_ids"] = list(current_ids)[:50]
            save_settings(settings)
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

            from_raw     = headers.get("from", "")
            from_address = _extract_email_address(from_raw).lower()

            # Skip emails from watched senders — email_watcher already handles these
            if watched_senders and from_address in watched_senders:
                logger.info(f"[inbox_checker] Skipping watched sender: {from_address}")
                continue

            new_emails.append({
                "from":    from_raw or "Unknown",
                "subject": headers.get("subject", "(no subject)"),
                "date":    headers.get("date", ""),
            })

        if new_emails:
            lines = [f"📬 *{len(new_emails)} new unread email(s) in your inbox:*\n"]
            for e in new_emails:
                lines.append(f"• *From:* {e['from']} | *Subject:* {e['subject']}")

            try:
                slack_client.chat_postMessage(
                    channel=autonomous_channel_id,
                    text="\n".join(lines),
                    mrkdwn=True,
                )
                logger.info(f"[inbox_checker] Notified {len(new_emails)} new emails.")
                log("inbox_checker", f"{len(new_emails)} new unread email(s) found", "✅")
            except Exception as e:
                logger.error(f"[inbox_checker] Slack post failed: {e}")
                log("inbox_checker", "Failed to post inbox notification", "❌")
        else:
            logger.info("[inbox_checker] All new emails were from watched senders — skipping inbox notification.")

        # Always update last_seen_ids to the current full set
        settings["inbox_check"]["last_seen_ids"] = list(current_ids)[:50]
        save_settings(settings)

    except Exception as e:
        logger.error(f"[inbox_checker] Error: {e}")
        log("inbox_checker", f"Inbox check failed: {e}", "❌")


def _extract_email_address(from_header: str) -> str:
    """
    Extract the raw email address from a From header.
    Handles both 'Name <email@domain.com>' and 'email@domain.com' formats.
    """
    import re
    match = re.search(r"<([^>]+)>", from_header)
    if match:
        return match.group(1).strip()
    return from_header.strip()
