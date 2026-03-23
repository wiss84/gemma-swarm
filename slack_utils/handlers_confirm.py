"""
Gemma Swarm — Confirmation Button Handlers
============================================
confirm_approve / confirm_reject for generic human gate confirmations
(e.g. file deletion).
"""

import logging
from nodes.human_gate import resolve_confirmation

logger = logging.getLogger(__name__)


def register_confirm_handlers(app):
    """Register confirm button handlers on the Slack Bolt app."""

    @app.action("confirm_approve")
    def handle_confirm_approve(ack, body, client):
        ack()
        thread_ts = body["actions"][0]["value"]
        channel   = body["channel"]["id"]
        resolve_confirmation(thread_ts, "approved")
        try:
            client.chat_delete(channel=channel, ts=body["message"]["ts"])
        except Exception:
            pass

    @app.action("confirm_reject")
    def handle_confirm_reject(ack, body, client):
        ack()
        thread_ts = body["actions"][0]["value"]
        channel   = body["channel"]["id"]
        resolve_confirmation(thread_ts, "rejected")
        try:
            client.chat_delete(channel=channel, ts=body["message"]["ts"])
        except Exception:
            pass
