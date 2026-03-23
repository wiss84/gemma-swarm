"""
Gemma Swarm — Email Button Handlers
======================================
email_approve / email_reject_feedback / email_feedback_modal
for the email draft review flow.
"""

import logging
from nodes.human_gate import resolve_confirmation, build_feedback_modal

logger = logging.getLogger(__name__)


def register_email_handlers(app):
    """Register email review handlers on the Slack Bolt app."""

    @app.action("email_approve")
    def handle_email_approve(ack, body, client):
        ack()
        thread_ts = body["actions"][0]["value"]
        channel   = body["channel"]["id"]
        resolve_confirmation(thread_ts, "approved")
        try:
            client.chat_delete(channel=channel, ts=body["message"]["ts"])
        except Exception:
            pass

    @app.action("email_reject_feedback")
    def handle_email_reject_feedback(ack, body, client):
        ack()
        trigger_id = body["trigger_id"]
        thread_ts  = body["actions"][0]["value"]
        channel    = body["channel"]["id"]
        msg_ts     = body["message"]["ts"]
        modal      = build_feedback_modal(thread_ts)
        try:
            client.views_open(trigger_id=trigger_id, view=modal)
            client.chat_delete(channel=channel, ts=msg_ts)
        except Exception as e:
            logger.error(f"[slack] Could not open feedback modal: {e}")
            resolve_confirmation(thread_ts, "rejected")

    @app.view("email_feedback_modal")
    def handle_email_feedback_submit(ack, body, view):
        ack()
        thread_ts = view["private_metadata"]
        feedback  = view["state"]["values"]["feedback_block"]["feedback_input"]["value"] or ""
        decision  = f"rejected: {feedback.strip()}" if feedback.strip() else "rejected"
        resolve_confirmation(thread_ts, decision)
