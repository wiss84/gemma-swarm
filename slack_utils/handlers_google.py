"""
Gemma Swarm — Google Button Handlers
=======================================
google_approve / google_reject_feedback / google_feedback_modal
for the Google write action review flow.

Mirrors the pattern of handlers_email.py and handlers_linkedin.py exactly.
"""

import logging
from nodes.human_gate import resolve_confirmation, build_google_feedback_modal

logger = logging.getLogger(__name__)


def register_google_handlers(app):
    """Register Google review handlers on the Slack Bolt app."""

    @app.action("google_approve")
    def handle_google_approve(ack, body, client):
        ack()
        thread_ts = body["actions"][0]["value"]
        channel   = body["channel"]["id"]
        resolve_confirmation(thread_ts, "approved")
        try:
            client.chat_delete(channel=channel, ts=body["message"]["ts"])
        except Exception:
            pass

    @app.action("google_reject_feedback")
    def handle_google_reject_feedback(ack, body, client):
        ack()
        trigger_id = body["trigger_id"]
        thread_ts  = body["actions"][0]["value"]
        channel    = body["channel"]["id"]
        msg_ts     = body["message"]["ts"]
        modal      = build_google_feedback_modal(thread_ts)
        try:
            client.views_open(trigger_id=trigger_id, view=modal)
            client.chat_delete(channel=channel, ts=msg_ts)
        except Exception as e:
            logger.error(f"[google_handlers] Could not open feedback modal: {e}")
            resolve_confirmation(thread_ts, "rejected")

    @app.view("google_feedback_modal")
    def handle_google_feedback_submit(ack, body, view):
        ack()
        thread_ts = view["private_metadata"]
        feedback  = view["state"]["values"]["feedback_block"]["feedback_input"]["value"] or ""
        decision  = f"rejected: {feedback.strip()}" if feedback.strip() else "rejected"
        resolve_confirmation(thread_ts, decision)
