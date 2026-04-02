"""
Gemma Swarm — Autonomous Settings Handlers
============================================
Handles the autonomous settings modal:
- Opens modal with current settings pre-filled
- Validates channel name on submit (shows inline error if not found)
- Saves settings to autonomous_settings.json on successful submit
- Creates activity log sheet on first save if needed
"""

import logging

logger = logging.getLogger(__name__)


def register_autonomous_handlers(app):
    """Register autonomous settings action and view handlers."""

    @app.action("open_autonomous_settings")
    def handle_open_autonomous_settings(ack, body, client):
        ack()
        trigger_id = body["trigger_id"]
        try:
            modal = _build_modal()
            client.views_open(trigger_id=trigger_id, view=modal)
        except Exception as e:
            logger.error(f"[handlers_autonomous] Could not open modal: {e}")

    @app.view("autonomous_settings_modal")
    def handle_autonomous_settings_submit(ack, body, view, client):
        """Validate and save autonomous settings."""
        values = view["state"]["values"]

        # ── Extract values ─────────────────────────────────────────────────────
        active = _get_toggle(values, "active_block", "active_input")

        channel_name = _get_text(values, "channel_block", "channel_input").strip().lstrip("#")

        email_senders_raw = _get_text(values, "email_senders_block", "email_senders_input")
        email_senders     = [s.strip() for s in email_senders_raw.split(",") if s.strip()]

        watch_interval    = int(_get_option(values, "watch_interval_block", "watch_interval_input") or 15)

        inbox_enabled     = _get_toggle(values, "inbox_enabled_block", "inbox_enabled_input")
        inbox_interval    = int(_get_option(values, "inbox_interval_block", "inbox_interval_input") or 30)

        topic_1           = _get_text(values, "topic_1_block", "topic_1_input").strip()
        topic_2           = _get_text(values, "topic_2_block", "topic_2_input").strip()
        topics            = [t for t in [topic_1, topic_2] if t]

        research_interval = int(_get_option(values, "research_interval_block", "research_interval_input") or 3)
        cal_minutes       = int(_get_option(values, "cal_minutes_block", "cal_minutes_input") or 30)

        # ── Validate channel ───────────────────────────────────────────────────
        if active and not channel_name:
            ack(response_action="errors", errors={
                "channel_block": "Please enter a channel name when autonomous mode is active."
            })
            return

        channel_id = ""
        if channel_name:
            from autonomous.settings import get_channel_id
            channel_id = get_channel_id(channel_name, client)
            if not channel_id:
                ack(response_action="errors", errors={
                    "channel_block": (
                        f"Channel '#{channel_name}' not found. "
                        "Please create it in Slack first, then try again."
                    )
                })
                return

        # ── All valid — save settings ──────────────────────────────────────────
        ack()

        from autonomous.settings import load_settings, save_settings
        settings = load_settings()

        settings["active"]                              = active
        settings["autonomous_channel"]                  = channel_name
        settings["autonomous_channel_id"]               = channel_id
        settings["email_watch"]["senders"]              = email_senders
        settings["email_watch"]["poll_interval_minutes"] = watch_interval
        settings["inbox_check"]["enabled"]              = inbox_enabled
        settings["inbox_check"]["poll_interval_minutes"] = inbox_interval
        settings["research"]["topics"]                  = topics
        settings["research"]["interval_days"]           = research_interval
        settings["calendar_notify"]["minutes_before"]   = cal_minutes

        # Create activity log sheet on first save if not yet created
        if not settings.get("activity_log_sheet_id") and channel_id:
            try:
                from autonomous.jobs.activity_logger import log
                log("system", "Autonomous pipeline initialized", "✅")
                # log() will create the sheet and save its ID automatically
                settings = load_settings()  # Reload to get the new sheet ID
            except Exception as e:
                logger.error(f"[handlers_autonomous] Could not create activity log sheet: {e}")

        save_settings(settings)
        logger.info(f"[handlers_autonomous] Settings saved. Active: {active}, Channel: #{channel_name}")

        # Post confirmation to the autonomous channel
        if channel_id:
            status = "✅ *Autonomous mode activated!*" if active else "⏸️ *Autonomous mode paused.*"
            try:
                client.chat_postMessage(
                    channel=channel_id,
                    text=(
                        f"{status}\n"
                        f"This channel will receive autonomous updates from Gemma Swarm.\n"
                        f"Watching senders: {', '.join(email_senders) if email_senders else 'none'}\n"
                        f"Research topics: {', '.join(topics) if topics else 'none'}"
                    ),
                    mrkdwn=True,
                )
            except Exception as e:
                logger.error(f"[handlers_autonomous] Could not post to channel: {e}")


# ── Modal Builder ──────────────────────────────────────────────────────────────

def _build_modal() -> dict:
    """Build the autonomous settings modal with current values pre-filled."""
    from autonomous.settings import load_settings
    s = load_settings()

    active          = s.get("active", False)
    channel         = s.get("autonomous_channel", "")
    senders         = ", ".join(s["email_watch"].get("senders", []))
    watch_interval  = str(s["email_watch"].get("poll_interval_minutes", 15))
    inbox_enabled   = s["inbox_check"].get("enabled", False)
    inbox_interval  = str(s["inbox_check"].get("poll_interval_minutes", 30))
    topics          = s["research"].get("topics", ["", ""])
    topic_1         = topics[0] if len(topics) > 0 else ""
    topic_2         = topics[1] if len(topics) > 1 else ""
    research_days   = str(s["research"].get("interval_days", 3))
    cal_minutes     = str(s["calendar_notify"].get("minutes_before", 30))

    return {
        "type":             "modal",
        "callback_id":      "autonomous_settings_modal",
        "title":            {"type": "plain_text", "text": "⚡ Autonomous Settings"},
        "submit":           {"type": "plain_text", "text": "Save"},
        "close":            {"type": "plain_text", "text": "Cancel"},
        "blocks": [

            # ── Active toggle ──────────────────────────────────────────────────
            {
                "type":    "input",
                "block_id": "active_block",
                "optional": True,
                "label":   {"type": "plain_text", "text": "Autonomous Mode"},
                "element": {
                    "type":      "checkboxes",
                    "action_id": "active_input",
                    "options": [{
                        "text":  {"type": "plain_text", "text": "Activate autonomous mode"},
                        "value": "active",
                    }],
                    "initial_options": ([{
                        "text":  {"type": "plain_text", "text": "Activate autonomous mode"},
                        "value": "active",
                    }] if active else []),
                },
            },

            # ── Channel ────────────────────────────────────────────────────────
            {
                "type":    "input",
                "block_id": "channel_block",
                "optional": False,
                "label":   {"type": "plain_text", "text": "📢 Autonomous Updates Channel"},
                "hint":    {"type": "plain_text", "text": "Channel where autonomous updates will be posted. Must already exist in Slack."},
                "element": {
                    "type":            "plain_text_input",
                    "action_id":       "channel_input",
                    "placeholder":     {"type": "plain_text", "text": "e.g. autonomous-updates"},
                    "initial_value":   channel,
                },
            },

            {"type": "divider"},

            # ── Email watcher ──────────────────────────────────────────────────
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*✉️ Email Watcher*\nWatch for new emails from specific senders."},
            },
            {
                "type":    "input",
                "block_id": "email_senders_block",
                "optional": True,
                "label":   {"type": "plain_text", "text": "Senders to watch (comma separated)"},
                "hint":    {"type": "plain_text", "text": "e.g. boss@company.com, client@domain.com"},
                "element": {
                    "type":          "plain_text_input",
                    "action_id":     "email_senders_input",
                    "placeholder":   {"type": "plain_text", "text": "Leave empty to disable"},
                    "initial_value": senders,
                },
            },
            {
                "type":    "input",
                "block_id": "watch_interval_block",
                "optional": True,
                "label":   {"type": "plain_text", "text": "Check every (minutes)"},
                "element": {
                    "type":            "static_select",
                    "action_id":       "watch_interval_input",
                    "initial_option":  {"text": {"type": "plain_text", "text": watch_interval}, "value": watch_interval},
                    "options":         _minute_options([5, 10, 15, 30, 60]),
                },
            },

            {"type": "divider"},

            # ── Inbox checker ──────────────────────────────────────────────────
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*📬 Inbox Checker*\nCheck for all new unread emails periodically."},
            },
            {
                "type":    "input",
                "block_id": "inbox_enabled_block",
                "optional": True,
                "label":   {"type": "plain_text", "text": "Inbox Checker"},
                "element": {
                    "type":      "checkboxes",
                    "action_id": "inbox_enabled_input",
                    "options": [{
                        "text":  {"type": "plain_text", "text": "Enable inbox checker"},
                        "value": "enabled",
                    }],
                    "initial_options": ([{
                        "text":  {"type": "plain_text", "text": "Enable inbox checker"},
                        "value": "enabled",
                    }] if inbox_enabled else []),
                },
            },
            {
                "type":    "input",
                "block_id": "inbox_interval_block",
                "optional": True,
                "label":   {"type": "plain_text", "text": "Check inbox every (minutes)"},
                "element": {
                    "type":           "static_select",
                    "action_id":      "inbox_interval_input",
                    "initial_option": {"text": {"type": "plain_text", "text": inbox_interval}, "value": inbox_interval},
                    "options":        _minute_options([15, 30, 60, 120]),
                },
            },

            {"type": "divider"},

            # ── Research ───────────────────────────────────────────────────────
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*🔍 Research & LinkedIn Drafts*\nAutomatically research topics and generate LinkedIn post drafts."},
            },
            {
                "type":    "input",
                "block_id": "topic_1_block",
                "optional": True,
                "label":   {"type": "plain_text", "text": "Research topic 1"},
                "element": {
                    "type":          "plain_text_input",
                    "action_id":     "topic_1_input",
                    "placeholder":   {"type": "plain_text", "text": "e.g. AI agents"},
                    "initial_value": topic_1,
                },
            },
            {
                "type":    "input",
                "block_id": "topic_2_block",
                "optional": True,
                "label":   {"type": "plain_text", "text": "Research topic 2"},
                "element": {
                    "type":          "plain_text_input",
                    "action_id":     "topic_2_input",
                    "placeholder":   {"type": "plain_text", "text": "e.g. LangGraph workflows"},
                    "initial_value": topic_2,
                },
            },
            {
                "type":    "input",
                "block_id": "research_interval_block",
                "optional": True,
                "label":   {"type": "plain_text", "text": "Run research every (days)"},
                "element": {
                    "type":           "static_select",
                    "action_id":      "research_interval_input",
                    "initial_option": {"text": {"type": "plain_text", "text": research_days}, "value": research_days},
                    "options":        _day_options([1, 2, 3, 5, 7]),
                },
            },

            {"type": "divider"},

            # ── Calendar ───────────────────────────────────────────────────────
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*📅 Calendar Reminders*\nGet notified before calendar events."},
            },
            {
                "type":    "input",
                "block_id": "cal_minutes_block",
                "optional": True,
                "label":   {"type": "plain_text", "text": "Notify me before events (minutes)"},
                "element": {
                    "type":           "static_select",
                    "action_id":      "cal_minutes_input",
                    "initial_option": {"text": {"type": "plain_text", "text": cal_minutes}, "value": cal_minutes},
                    "options":        _minute_options([5, 10, 15, 30, 60]),
                },
            },
        ],
    }


# ── Option Builders ────────────────────────────────────────────────────────────

def _minute_options(values: list[int]) -> list[dict]:
    return [
        {"text": {"type": "plain_text", "text": str(v)}, "value": str(v)}
        for v in values
    ]

def _day_options(values: list[int]) -> list[dict]:
    return [
        {"text": {"type": "plain_text", "text": str(v)}, "value": str(v)}
        for v in values
    ]


# ── Value Extractors ───────────────────────────────────────────────────────────

def _get_text(values: dict, block_id: str, action_id: str) -> str:
    try:
        return values[block_id][action_id].get("value") or ""
    except (KeyError, AttributeError):
        return ""

def _get_option(values: dict, block_id: str, action_id: str) -> str:
    try:
        return values[block_id][action_id]["selected_option"]["value"]
    except (KeyError, TypeError):
        return ""

def _get_toggle(values: dict, block_id: str, action_id: str) -> bool:
    try:
        selected = values[block_id][action_id].get("selected_options", [])
        return len(selected) > 0
    except (KeyError, AttributeError):
        return False
