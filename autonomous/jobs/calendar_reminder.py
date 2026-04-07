"""
Gemma Swarm — Autonomous Calendar Reminder
============================================
Runs on app startup and then hourly via the scheduler.
Fetches calendar events for the next 48 hours.
For each event, creates pending notification entries for every user-selected offset.
Notifications are fired by the scheduler tick when their fire_at time arrives.
Zero LLM calls.
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def run(slack_client, autonomous_channel_id: str):
    """
    Fetch calendar events for the next 48 hours and populate the
    pending_notifications queue. Safe to call repeatedly — existing
    entries for the same event+offset are not duplicated.
    """
    from autonomous.settings import load_settings, save_settings
    from autonomous.jobs.activity_logger import log

    settings = load_settings()
    offsets  = settings["calendar_notify"].get("notify_offsets", [30])
    pending  = settings["calendar_notify"].get("pending_notifications", [])

    try:
        from tools.calendar_api import calendar_list_events

        now       = datetime.now(timezone.utc)
        window_min = now.isoformat()
        window_max = (now + timedelta(hours=48)).isoformat()

        events = calendar_list_events(
            max_results=50,
            start_date=window_min,
            end_date=window_max,
        )

        if not events:
            logger.info("[calendar_reminder] No events in the next 48 hours.")
        else:
            existing_keys = {
                (n["event_id"], n["offset_minutes"])
                for n in pending
            }

            new_count = 0
            for event in events:
                event_id    = event.get("id", "")
                title       = event.get("title", "(no title)")
                start       = event.get("start", "")
                location    = event.get("location", "")
                description = event.get("description", "")

                if not start or not event_id:
                    continue

                start_dt = _parse_datetime(start)
                if not start_dt:
                    logger.warning(f"[calendar_reminder] Could not parse start time for: {title}")
                    continue

                # Skip events that have already started — no point queuing any offset for them
                if start_dt <= now:
                    logger.info(f"[calendar_reminder] Skipping past event: {title} (started {start_dt})")
                    continue

                for offset in offsets:
                    key = (event_id, offset)
                    if key in existing_keys:
                        continue

                    fire_at = start_dt - timedelta(minutes=offset)
                    if fire_at < now:
                        # This specific notification window has passed — skip it
                        logger.info(
                            f"[calendar_reminder] Skipping missed window ({offset}m) for: {title}"
                        )
                        continue

                    pending.append({
                        "event_id":       event_id,
                        "event_title":    title,
                        "event_start":    start_dt.isoformat(),
                        "offset_minutes": offset,
                        "fire_at":        fire_at.isoformat(),
                        "fired":          False,
                        "location":       location,
                        "description":    description,
                    })
                    existing_keys.add(key)
                    new_count += 1

            if new_count:
                logger.info(f"[calendar_reminder] Added {new_count} new notification(s) to the queue.")
                log("calendar_reminder", f"Queued {new_count} new notification(s)", "✅")
            else:
                logger.info("[calendar_reminder] No new notifications to add.")

        cutoff = now - timedelta(hours=2)
        pending = [
            n for n in pending
            if (n.get("fired") and (fa := _parse_datetime(n["fire_at"])) is not None and fa >= cutoff)
            or not n.get("fired")
        ]

        settings["calendar_notify"]["pending_notifications"] = pending
        settings["calendar_notify"]["last_checked_date"] = now.date().isoformat()
        save_settings(settings)

    except Exception as e:
        logger.error(f"[calendar_reminder] Error: {e}")
        log("calendar_reminder", f"Calendar check failed: {e}", "❌")


def fire_due_notifications(slack_client, autonomous_channel_id: str):
    """
    Called by the scheduler on every tick. Fires any pending notifications
    whose fire_at time has arrived, then marks them as fired and prunes old entries.
    """
    from autonomous.settings import load_settings, save_settings

    settings = load_settings()
    pending  = settings["calendar_notify"].get("pending_notifications", [])
    now      = datetime.now(timezone.utc)

    fired_any = False
    for entry in pending:
        if entry.get("fired"):
            continue

        # Guard: if the event itself has already started, mark as dead and skip.
        # This handles the case where the app was restarted after the event time —
        # stale unfired entries should never produce notifications.
        event_start_dt = _parse_datetime(entry.get("event_start", ""))
        if event_start_dt and event_start_dt <= now:
            logger.info(
                f"[calendar_reminder] Discarding stale notification for past event: "
                f"{entry.get('event_title')} (offset {entry.get('offset_minutes')}m)"
            )
            entry["fired"] = True
            fired_any = True
            continue

        fire_at = _parse_datetime(entry.get("fire_at", ""))
        if not fire_at or fire_at > now:
            continue

        entry["fired"] = True
        fired_any = True

        offset     = entry["offset_minutes"]
        title      = entry.get("event_title", "(no title)")
        start_str  = entry.get("event_start", "")
        location   = entry.get("location", "")
        desc       = entry.get("description", "")

        start_dt = _parse_datetime(start_str)
        time_label = _format_minutes(offset)
        local_time = _format_local_time(start_dt) if start_dt else "unknown"

        lines = [
            f"📅 *Upcoming event in {time_label}:*",
            f"*{title}*",
            f"🕐 {local_time}",
        ]
        if location:
            lines.append(f"📍 {location}")
        if desc:
            lines.append(f"📝 {desc[:200]}")

        try:
            slack_client.chat_postMessage(
                channel=autonomous_channel_id,
                text="\n".join(lines),
                mrkdwn=True,
            )
            logger.info(f"[calendar_reminder] Notification fired: {title} ({time_label} before)")
        except Exception as e:
            logger.error(f"[calendar_reminder] Slack post failed for '{title}': {e}")

    if fired_any:
        cutoff = now - timedelta(hours=2)
        pending = [
            n for n in pending
            if not n.get("fired")
            or ((fa := _parse_datetime(n.get("fire_at", ""))) is not None and fa >= cutoff)
        ]
        settings["calendar_notify"]["pending_notifications"] = pending
        save_settings(settings)


def _parse_datetime(dt_str: str) -> datetime | None:
    """Parse ISO 8601 datetime string to timezone-aware UTC datetime."""
    if not dt_str:
        return None
    try:
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _format_local_time(dt: datetime) -> str:
    """Format datetime in the user's local timezone for display."""
    try:
        from tools.google_api import get_user_timezone
        import zoneinfo
        user_tz  = get_user_timezone()
        local_dt = dt.astimezone(zoneinfo.ZoneInfo(user_tz))
        return local_dt.strftime("%I:%M %p") + f" ({user_tz})"
    except Exception:
        return dt.strftime("%I:%M %p UTC")


def _format_minutes(minutes: int) -> str:
    """Format minutes into a human-readable string."""
    if minutes < 60:
        return f"{minutes} minutes"
    hours     = minutes // 60
    remaining = minutes % 60
    if remaining == 0:
        return f"{hours} hour{'s' if hours > 1 else ''}"
    return f"{hours}h {remaining}m"
