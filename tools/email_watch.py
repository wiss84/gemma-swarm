"""
Gemma Swarm — Email Watch
=========================
Background polling to watch for incoming emails from specific senders.
Posts a Slack notification when an email is received.
Uses OAuth helpers from google_api.py.
"""

import logging
import threading
import requests

# Import shared auth helpers from google_api
from tools.google_api import _get_access_token, _auth_headers

logger = logging.getLogger(__name__)


# ── Active Watches Tracking ───────────────────────────────────────────────────

_active_watches: dict = {}
_watches_lock         = threading.Lock()


def start_email_watch(
    sender_email: str,
    slack_client,
    channel: str,
    thread_ts: str,
    poll_interval_seconds: int = 300,
) -> bool:
    """
    Start a background polling thread that checks Gmail every 10 minutes
    for an unread email from sender_email.
    Posts a Slack notification when found and stops automatically.
    Returns False if a watch for this sender already exists.
    """
    with _watches_lock:
        if sender_email in _active_watches:
            return False

        stop_event = threading.Event()
        _active_watches[sender_email] = {
            "thread_ts":  thread_ts,
            "channel":    channel,
            "stop_event": stop_event,
        }

    def _poll():
        logger.info(f"[google/watch] Started watching for: {sender_email}")
        while not stop_event.is_set():
            try:
                # gmail_check_for_sender now returns full body if found
                # but for the watch notification we only need subject and date
                # so we use a lightweight metadata check here instead
                token  = _get_access_token()
                params = {
                    "maxResults": 1,
                    "q":          f"from:{sender_email} is:unread",
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

                if messages:
                    # Fetch metadata only for the notification
                    detail = requests.get(
                        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{messages[0]['id']}",
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
                    notify = (
                        f"📬 *Email received from {sender_email}*\n"
                        f"*Subject:* {headers.get('subject', '(no subject)')}\n"
                        f"*Date:* {headers.get('date', '')}"
                    )
                    try:
                        slack_client.chat_postMessage(
                            channel=channel,
                            thread_ts=thread_ts,
                            text=notify,
                            mrkdwn=True,
                        )
                    except Exception as e:
                        logger.error(f"[google/watch] Slack notify failed: {e}")

                    stop_email_watch(sender_email)
                    return

            except Exception as e:
                logger.error(f"[google/watch] Poll error: {e}")

            stop_event.wait(timeout=poll_interval_seconds)

        logger.info(f"[google/watch] Stopped watching: {sender_email}")

    threading.Thread(
        target=_poll,
        daemon=True,
        name=f"gmail_watch_{sender_email}",
    ).start()
    return True


def stop_email_watch(sender_email: str) -> bool:
    """
    Stop watching for emails from a specific sender.
    Returns True if a watch was stopped, False if no active watch exists.
    """
    with _watches_lock:
        watch = _active_watches.pop(sender_email, None)
    if watch:
        watch["stop_event"].set()
        logger.info(f"[google/watch] Watch stopped: {sender_email}")
        return True
    return False


def list_active_watches() -> list[str]:
    """
    List all email addresses currently being watched.
    """
    with _watches_lock:
        return list(_active_watches.keys())
