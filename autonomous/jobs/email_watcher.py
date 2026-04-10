"""
Gemma Swarm — Autonomous Email Watcher
========================================
Polls Gmail for new unread emails from configured senders.
Compares against stored last-seen message IDs — only notifies on new emails.
Updates last-seen IDs after each run so emails are never repeated.

If reply_drafts_enabled is True in settings:
  - Fetches the full body of each new email
  - Passes it to the LLM to classify importance and draft a reply if warranted
  - Creates a Google Doc per email with the draft reply
  - Posts the doc link to the autonomous channel
  2 LLM calls per important email (classify + draft).
"""

import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)

SLEEP_BETWEEN_LLM = 8


def _format_email_date(raw_date: str) -> str:
    """
    Parse a raw RFC 2822 email Date header and return a clean local-time string.
    Falls back to the raw string if parsing fails.
    """
    if not raw_date:
        return ""
    try:
        import zoneinfo
        from tools.google_api import get_user_timezone
        dt      = parsedate_to_datetime(raw_date)
        user_tz = get_user_timezone()
        local   = dt.astimezone(zoneinfo.ZoneInfo(user_tz))
        return local.strftime("%a, %b %d %Y %I:%M %p") + f" ({user_tz})"
    except Exception:
        return raw_date


def _get_user_name() -> str:
    """Read the user's name from user_preferences.json. Returns empty string if not set."""
    try:
        import json
        from pathlib import Path
        prefs_file = Path(__file__).parent.parent.parent / "user_preferences.json"
        if prefs_file.exists():
            prefs = json.loads(prefs_file.read_text(encoding="utf-8"))
            return prefs.get("name", "").strip()
    except Exception:
        pass
    return ""


def _fetch_email_body(msg_id: str, token: str) -> str:
    """
    Fetch the plain-text body of a Gmail message.
    Returns truncated body string, or empty string on failure.
    """
    import requests
    import base64
    from tools.google_api import _auth_headers

    try:
        response = requests.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
            headers=_auth_headers(token),
            params={"format": "full"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        def _extract_parts(payload: dict) -> str:
            """Recursively extract plain text from message parts."""
            mime = payload.get("mimeType", "")
            body_data = payload.get("body", {}).get("data", "")

            if mime == "text/plain" and body_data:
                return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")

            # Recurse into multipart
            for part in payload.get("parts", []):
                text = _extract_parts(part)
                if text:
                    return text
            return ""

        body = _extract_parts(data.get("payload", {}))
        return body[:4000].strip()  # cap to avoid overflowing LLM context

    except Exception as e:
        logger.error(f"[email_watcher] Could not fetch body for {msg_id}: {e}")
        return ""


def _classify_and_draft(
    sender: str,
    subject: str,
    body: str,
    local_date: str,
    slack_client,
    autonomous_channel_id: str,
):
    """
    Run the 2-step LLM flow for a single email:
      Step 1 — classify: is this email important enough to draft a reply?
      Step 2 — if yes: draft the reply and create a Google Doc

    Important emails are those that require a personal response:
    recruiters, meeting requests, professional enquiries, client questions, etc.
    Newsletters, automated alerts, and marketing emails are not important.
    """
    from autonomous import pipeline_agent
    from autonomous.jobs.activity_logger import log
    from tools.docs_api import docs_create_formatted

    user_name = _get_user_name()
    name_context = f"The recipient's name is {user_name}." if user_name else ""

    # ── Step 1: Classify ───────────────────────────────────────────────────────
    classify_prompt = (
        f"You are an email assistant. Determine if the following email requires a personal reply.\n\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Date: {local_date}\n"
        f"Body:\n{body}\n\n"
        f"Emails that REQUIRE a reply (important):\n"
        f"- Recruiter or hiring manager reaching out about a job opportunity\n"
        f"- Client or colleague asking a professional question\n"
        f"- Meeting request or scheduling question\n"
        f"- Direct personal or business enquiry that expects a response\n\n"
        f"Emails that do NOT require a reply (not important):\n"
        f"- Newsletters, digests, automated reports\n"
        f"- Marketing or promotional emails\n"
        f"- Automated notifications (order confirmations, alerts, receipts)\n"
        f"- No-reply senders\n\n"
        f"Respond with ONLY one word: IMPORTANT or NOT_IMPORTANT"
    )

    time.sleep(SLEEP_BETWEEN_LLM)
    classification = pipeline_agent.ask(classify_prompt).strip().upper()

    if "IMPORTANT" not in classification:
        logger.info(f"[email_watcher] Email classified as not important: {subject}")
        return

    logger.info(f"[email_watcher] Email classified as IMPORTANT — drafting reply: {subject}")

    # ── Step 2: Draft reply ────────────────────────────────────────────────────
    draft_prompt = (
        f"You are an email assistant helping draft a professional reply.\n"
        f"{name_context}\n\n"
        f"Original email:\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Date: {local_date}\n"
        f"Body:\n{body}\n\n"
        f"Write a professional, concise reply draft.\n"
        f"Rules:\n"
        f"- Polite and professional tone\n"
        f"- Directly address what the sender is asking\n"
        f"- Do not invent facts or commitments — use placeholders like [your availability] "
        f"or [your answer here] where the user needs to fill in specifics\n"
        f"- Keep it under 150 words\n"
        f"- Start with an appropriate greeting\n"
        f"- End with an appropriate sign-off\n"
        f"- Output ONLY the reply text, nothing else"
    )

    time.sleep(SLEEP_BETWEEN_LLM)
    draft = pipeline_agent.ask(draft_prompt)

    if not draft or draft.startswith("[LLM error"):
        logger.error(f"[email_watcher] Draft generation failed for: {subject}")
        log("email_watcher", f"Reply draft failed for: {subject}", "❌")
        return

    # ── Create Google Doc ──────────────────────────────────────────────────────
    date_str  = datetime.now().strftime("%Y-%m-%d")
    doc_title = f"Email Reply Draft — {subject[:60]} — {date_str}"
    doc_content = (
        f"## Email Reply Draft\n\n"
        f"**From:** {sender}\n"
        f"**Subject:** {subject}\n"
        f"**Received:** {local_date}\n\n"
        f"---\n\n"
        f"## Original Email\n\n"
        f"{body}\n\n"
        f"---\n\n"
        f"## Suggested Reply\n\n"
        f"{draft}"
    )

    try:
        doc  = docs_create_formatted(title=doc_title, content=doc_content)
        link = doc["link"]
        logger.info(f"[email_watcher] Reply draft doc created: {link}")
    except Exception as e:
        logger.error(f"[email_watcher] Doc creation failed for '{subject}': {e}")
        log("email_watcher", f"Reply draft doc failed for: {subject}", "❌")
        return

    # ── Post to Slack ──────────────────────────────────────────────────────────
    try:
        slack_client.chat_postMessage(
            channel=autonomous_channel_id,
            text=(
                f"📝 *Reply draft ready*\n"
                f"*From:* {sender}\n"
                f"*Subject:* {subject}\n"
                f"📄 <{link}|Open Reply Draft — {date_str}>"
            ),
            mrkdwn=True,
        )
    except Exception as e:
        logger.error(f"[email_watcher] Slack post for draft failed: {e}")

    log("email_watcher", f"Reply draft created for: {subject}", "✅")


def run(slack_client, autonomous_channel_id: str):
    """
    Check Gmail for new unread emails from all configured watched senders.
    Posts a Slack notification for each new email found.
    If reply_drafts_enabled, also classifies and drafts replies for important emails.
    Updates last_seen_ids in settings after each check.
    """
    from autonomous.settings import load_settings, save_settings
    from autonomous.jobs.activity_logger import log
    from tools.google_api import _get_access_token, _auth_headers
    import requests

    settings      = load_settings()
    senders       = settings["email_watch"].get("senders", [])
    reply_drafts  = settings["email_watch"].get("reply_drafts_enabled", False)

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

            prev_seen = set(last_seen_ids.get(sender, []))
            new_msgs  = [m for m in messages if m["id"] not in prev_seen]

            if not new_msgs:
                logger.info(f"[email_watcher] No NEW emails from {sender} (already seen)")
                continue

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
                local_date = _format_email_date(raw_date)

                # ── Slack notification (always) ────────────────────────────────
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

                log("email_watcher", f"New email from {sender} — {subject}", "✅")

                # ── Reply draft (optional) ─────────────────────────────────────
                # Fetches the full email body, classifies importance with the LLM,
                # and drafts a reply doc if the email warrants a response.
                if reply_drafts:
                    body = _fetch_email_body(msg["id"], token)
                    if body:
                        _classify_and_draft(
                            sender=sender,
                            subject=subject,
                            body=body,
                            local_date=local_date,
                            slack_client=slack_client,
                            autonomous_channel_id=autonomous_channel_id,
                        )
                    else:
                        logger.warning(f"[email_watcher] Could not fetch body for '{subject}' — skipping draft")

            all_ids = list(prev_seen | {m["id"] for m in messages})
            last_seen_ids[sender] = all_ids[-50:]

        except Exception as e:
            logger.error(f"[email_watcher] Error checking {sender}: {e}")
            log("email_watcher", f"Error checking emails from {sender}", "❌")

    settings["email_watch"]["last_seen_ids"] = last_seen_ids
    save_settings(settings)

    if not found_any:
        logger.info("[email_watcher] Run complete — no new watched emails.")
