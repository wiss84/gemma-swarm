"""
Gemma Swarm — Autonomous Email Watcher
========================================
Polls Gmail for new unread emails from configured senders.
Compares against stored last-seen message IDs — only notifies on new emails.
Updates last-seen IDs after each run so emails are never repeated.
Zero LLM calls.
"""

import logging

logger = logging.getLogger(__name__)


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
            prev_seen  = set(last_seen_ids.get(sender, []))
            new_msgs   = [m for m in messages if m["id"] not in prev_seen]

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

                subject = headers.get("subject", "(no subject)")
                date    = headers.get("date", "")

                # Post Slack notification
                notify_text = (
                    f"📬 *New email from {sender}*\n"
                    f"*Subject:* {subject}\n"
                    f"*Date:* {date}"
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
