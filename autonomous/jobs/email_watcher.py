"""
Gemma Swarm — Autonomous Email Watcher
========================================
Polls Gmail for new unread emails from configured senders.
Compares against stored last-seen message IDs — only notifies on new emails.
Updates last-seen IDs after each run so emails are never repeated.
Zero LLM calls.
"""

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)


def _format_email_date(raw_date: str) -> str:
    """
    Parse a raw RFC 2822 email Date header and return a clean local-time string.
    Example input:  "Mon, 07 Apr 2026 12:20:00 +0000"
    Example output: "Mon, Apr 07 2026 02:20 PM (Europe/Warsaw)"
    Falls back to the raw string if parsing fails.
    """
    if not raw_date:
        return ""
    try:
        import zoneinfo
        from tools.google_api import get_user_timezone

        dt      = parsedate_to_datetime(raw_date)          # always tz-aware
        user_tz = get_user_timezone()                       # e.g. "Europe/Warsaw"
        local   = dt.astimezone(zoneinfo.ZoneInfo(user_tz))
        return local.strftime("%a, %b %d %Y %I:%M %p") + f" ({user_tz})"
    except Exception:
        return raw_date  # safe fallback — always show something


def run(slack_client, autonomous_channel_id: str):
    """
    Check Gmail for new unread emails from all configured watched senders.
    Posts a Slack notification for each new email found.
    Updates last_seen_ids in settings after each check.
    """
    from autonomous.settings import load_settings, save_settings
    from autonomous.jobs.activity_logger import log
    from tools.google_api import _get_access_token, _auth_headers
    import requests

    settings = load_settings()
    senders  = settings["email_watch"].get("senders", [])

    if not senders:
        logger.info("[email_watcher] No senders configured — skipping.")
        return

    last_seen_ids = settings["email_watch"].get("last_seen_ids", {})
    found_any     = False

    for sender in senders:
        sender = sender.strip()
        if not sender:
            continue

        try:
            token  = _get_access_token()
            params = {
                "maxResults": 5,
                "q":          f"from:{sender} is:unread",
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
                logger.info(f"[email_watcher] No new emails from {sender}")
                continue

            # Filter out already-seen message IDs
            prev_seen = set(last_seen_ids.get(sender, []))
            new_msgs  = [m for m in messages if m["id"] not in prev_seen]

            if not new_msgs:
                logger.info(f"[email_watcher] No NEW emails from {sender} (already seen)")
                continue

            # Fetch metadata for each new message
            for msg in new_msgs:
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

                subject    = headers.get("subject", "(no subject)")
                raw_date   = headers.get("date", "")
                # Convert the raw RFC 2822 date to the user's local timezone
                local_date = _format_email_date(raw_date)

                # Post Slack notification
                notify_text = (
                    f"📬 *New email from {sender}*\n"
                    f"*Subject:* {subject}\n"
                    f"*Date:* {local_date}"
                )
                try:
                    slack_client.chat_postMessage(
                        channel=autonomous_channel_id,
                        text=notify_text,
                        mrkdwn=True,
                    )
                    found_any = True
                except Exception as e:
                    logger.error(f"[email_watcher] Slack post failed: {e}")

                # Log activity
                log("email_watcher", f"New email from {sender} — {subject}", "✅")

            # Update last-seen IDs for this sender
            # Keep all current message IDs (including old ones) to avoid repeats
            all_ids = list(prev_seen | {m["id"] for m in messages})
            last_seen_ids[sender] = all_ids[-50:]  # Cap at 50 to avoid unbounded growth

        except Exception as e:
            logger.error(f"[email_watcher] Error checking {sender}: {e}")
            log("email_watcher", f"Error checking emails from {sender}", "❌")

    # Save updated last_seen_ids
    settings["email_watch"]["last_seen_ids"] = last_seen_ids
    save_settings(settings)

    if not found_any:
        logger.info("[email_watcher] Run complete — no new watched emails.")
