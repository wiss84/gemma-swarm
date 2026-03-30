"""
Gemma Swarm — Gmail API
=======================
Gmail API functions for reading, listing, and checking emails.
Uses OAuth helpers from google_api.py.
"""

import logging
import requests
from typing import Optional

# Import shared auth helpers from google_api
from tools.google_api import _get_access_token, _auth_headers, _extract_plain_text_body

logger = logging.getLogger(__name__)


def gmail_list_messages(
    max_results: int = 5,
    query: str = "",
    slack_post_fn=None,
) -> list[dict]:
    """
    List emails from Gmail inbox.
    Returns lightweight summary only: id, from, subject, date.
    Uses metadata format — no body fetching for speed and efficiency.
    query: Gmail search syntax e.g. "from:someone@domain.com is:unread"
    """
    token  = _get_access_token(slack_post_fn)
    params = {"maxResults": max_results, "labelIds": "INBOX"}
    if query:
        params["q"] = query

    response = requests.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        headers=_auth_headers(token),
        params=params,
        timeout=15,
    )
    response.raise_for_status()
    messages = response.json().get("messages", [])

    results = []
    for msg in messages:
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
        results.append({
            "id":      msg["id"],
            "from":    headers.get("from", ""),
            "subject": headers.get("subject", "(no subject)"),
            "date":    headers.get("date", ""),
        })

    logger.info(f"[google/gmail] Listed {len(results)} emails.")
    return results


def gmail_read_message(message_id: str, slack_post_fn=None) -> Optional[dict]:
    """
    Fetch the full plain text content of a single email by ID.
    Returns: id, from, subject, date, body (plain text only).
    """
    token    = _get_access_token(slack_post_fn)
    response = requests.get(
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
        headers=_auth_headers(token),
        params={"format": "full"},
        timeout=15,
    )
    response.raise_for_status()
    data    = response.json()
    headers = {
        h["name"].lower(): h["value"]
        for h in data.get("payload", {}).get("headers", [])
    }
    body = _extract_plain_text_body(data.get("payload", {}))

    logger.info(f"[google/gmail] Read message {message_id}.")
    return {
        "id":      message_id,
        "from":    headers.get("from", ""),
        "subject": headers.get("subject", "(no subject)"),
        "date":    headers.get("date", ""),
        "body":    body,
    }


def gmail_check_for_sender(sender_email: str, slack_post_fn=None) -> Optional[dict]:
    """
    Check if an unread email from a specific sender exists in inbox.
    If found: fetches and returns the full plain text body immediately.
    If not found: returns None.

    Returns: id, from, subject, date, body — or None if no email found.
    This combines check + read into a single operation so the agent
    does not need to chain two separate actions.
    """
    token  = _get_access_token(slack_post_fn)
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

    if not messages:
        return None

    # Email found — fetch full content immediately
    message_id = messages[0]["id"]
    logger.info(f"Found message id: {message_id}.")
    full = requests.get(
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
        headers=_auth_headers(token),
        params={"format": "full"},
        timeout=15,
    )
    full.raise_for_status()
    data    = full.json()
    headers = {
        h["name"].lower(): h["value"]
        for h in data.get("payload", {}).get("headers", [])
    }
    body = _extract_plain_text_body(data.get("payload", {}))

    logger.info(f"[google/gmail] Found and read email from {sender_email}.")
    logger.info(f"[google/gmail] Email body: {body}.")
    return {
        "id":      message_id,
        "from":    headers.get("from", ""),
        "subject": headers.get("subject", "(no subject)"),
        "date":    headers.get("date", ""),
        "body":    body,
    }
