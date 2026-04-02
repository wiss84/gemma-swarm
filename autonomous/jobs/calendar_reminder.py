"""
Gemma Swarm — Autonomous Calendar Reminder
============================================
Runs once per day on app startup.
Fetches today's Google Calendar events.
Schedules a Slack notification X minutes before each event.
Zero LLM calls.
"""

import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Track scheduled timers so we don't double-schedule on repeated runs
_scheduled_event_ids: set = set()
_scheduled_lock           = threading.Lock()


def run(slack_client, autonomous_channel_id: str):
    """
    Fetch today's calendar events and schedule a Slack reminder
    X minutes before each one. Only runs once per calendar day.
    """
    from autonomous.settings import load_settings, save_settings
    from autonomous.jobs.activity_logger import log

    settings       = load_settings()
    minutes_before = settings["calendar_notify"].get("minutes_before", 30)

    try:
        from tools.calendar_api import calendar_list_events

        # Fetch only today's events
        now       = datetime.now(timezone.utc)
        today_min = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        today_max = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

        events = calendar_list_events(
            max_results=20,
            start_date=today_min,
            end_date=today_max,
        )

        if not events:
            logger.info("[calendar_reminder] No events today.")
            log("calendar_reminder", "No events found for today", "⏭️ skipped")
        else:
            scheduled_count = 0
            for event in events:
                _schedule_reminder(
                    event=event,
                    slack_client=slack_client,
                    autonomous_channel_id=autonomous_channel_id,
                    minutes_before=minutes_before,
                )
                scheduled_count += 1

            logger.info(f"[calendar_reminder] Scheduled {scheduled_count} reminder(s).")
            log("calendar_reminder", f"Scheduled {scheduled_count} reminder(s) for today", "✅")

        # Update last_checked_date
        settings["calendar_notify"]["last_checked_date"] = now.date().isoformat()
        save_settings(settings)

    except Exception as e:
        logger.error(f"[calendar_reminder] Error: {e}")
        log("calendar_reminder", f"Calendar check failed: {e}", "❌")


def _schedule_reminder(event: dict, slack_client, autonomous_channel_id: str, minutes_before: int):
    """
    Schedule a threading.Timer to fire X minutes before the event start time.
    Skips events that have already passed or are too soon.
    """
    event_id = event.get("id", "")
    title    = event.get("title", "(no title)")
    start    = event.get("start", "")

    if not start or not event_id:
        return

    with _scheduled_lock:
        if event_id in _scheduled_event_ids:
            return  # Already scheduled this session

    try:
        # Parse event start time
        start_dt = _parse_datetime(start)
        if not start_dt:
            logger.warning(f"[calendar_reminder] Could not parse start time for: {title}")
            return

        now           = datetime.now(timezone.utc)
        reminder_time = start_dt - __import__("datetime").timedelta(minutes=minutes_before)
        delay_seconds = (reminder_time - now).total_seconds()

        if delay_seconds <= 0:
            # Event already started or reminder time has passed
            logger.info(f"[calendar_reminder] Skipping past event: {title}")
            return

        location    = event.get("location", "")
        description = event.get("description", "")

        def _send_reminder():
            lines = [
                f"📅 *Upcoming event in {minutes_before} minutes:*",
                f"*{title}*",
                f"🕐 {_format_time(start_dt)}",
            ]
            if location:
                lines.append(f"📍 {location}")
            if description:
                lines.append(f"📝 {description[:200]}")

            try:
                slack_client.chat_postMessage(
                    channel=autonomous_channel_id,
                    text="\n".join(lines),
                    mrkdwn=True,
                )
                logger.info(f"[calendar_reminder] Reminder sent: {title}")
            except Exception as e:
                logger.error(f"[calendar_reminder] Slack post failed for '{title}': {e}")

        timer = threading.Timer(delay_seconds, _send_reminder)
        timer.daemon = True
        timer.start()

        with _scheduled_lock:
            _scheduled_event_ids.add(event_id)

        logger.info(
            f"[calendar_reminder] Reminder scheduled for '{title}' "
            f"in {delay_seconds/60:.1f} minutes."
        )

    except Exception as e:
        logger.error(f"[calendar_reminder] Could not schedule reminder for '{title}': {e}")


def _parse_datetime(dt_str: str) -> datetime | None:
    """Parse ISO 8601 datetime string to timezone-aware datetime."""
    from datetime import timezone as tz
    try:
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz.utc)
        return dt
    except Exception:
        return None


def _format_time(dt: datetime) -> str:
    """Format datetime for display in Slack."""
    try:
        return dt.strftime("%I:%M %p UTC")
    except Exception:
        return str(dt)
