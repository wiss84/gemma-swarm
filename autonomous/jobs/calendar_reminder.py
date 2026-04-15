"""
Gemma Swarm — Autonomous Calendar Reminder
============================================
Runs on app startup and then hourly via the scheduler.
Fetches calendar events for the next 48 hours.
For each event, creates pending notification entries for every user-selected offset.
Notifications are fired by the scheduler tick when their fire_at time arrives.
Zero LLM calls (voice_llm option adds 1 LLM call per fired notification).
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# How many minutes past the fire_at time we still consider a notification "on time".
# Anything older than this is a missed window and gets silently dropped.
NOTIFICATION_LATE_TOLERANCE_MINUTES = 5


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

        now        = datetime.now(timezone.utc)
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

                # Skip events that have already started
                if start_dt <= now:
                    logger.info(f"[calendar_reminder] Skipping past event: {title} (started {start_dt})")
                    continue

                for offset in offsets:
                    key = (event_id, offset)
                    if key in existing_keys:
                        continue

                    fire_at = start_dt - timedelta(minutes=offset)
                    if fire_at < now:
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

    Race-condition note: the LLM voice call can take several seconds, during which
    the next scheduler tick might read the same pending_notifications from disk and
    see the entry as still unfired. To prevent double-firing, we mark ALL due entries
    as fired=True and flush to disk BEFORE making any Slack posts or LLM calls.
    The Slack/voice work then happens on the already-committed snapshot.
    """
    from autonomous.settings import load_settings, save_settings

    settings     = load_settings()
    pending      = settings["calendar_notify"].get("pending_notifications", [])
    voice_alerts = settings["calendar_notify"].get("voice_alerts", False)
    voice_llm    = settings["calendar_notify"].get("voice_llm", False)
    now          = datetime.now(timezone.utc)
    tolerance    = timedelta(minutes=NOTIFICATION_LATE_TOLERANCE_MINUTES)

    # ── Pass 1: Mark every due/stale entry as fired and save immediately ───────
    # This commits the state to disk before any slow operations (Slack API, LLM),
    # so concurrent ticks reading the file won't see these entries as pending.
    to_fire   = []  # entries to actually send notifications for
    changed   = False

    for entry in pending:
        if entry.get("fired"):
            continue

        # Guard 1: event has already started — discard silently
        event_start_dt = _parse_datetime(entry.get("event_start", ""))
        if event_start_dt and event_start_dt <= now:
            logger.info(
                f"[calendar_reminder] Discarding stale notification for past event: "
                f"{entry.get('event_title')} (offset {entry.get('offset_minutes')}m)"
            )
            entry["fired"] = True
            changed = True
            continue

        fire_at = _parse_datetime(entry.get("fire_at", ""))
        if not fire_at:
            continue

        # Guard 2: missed the window by more than tolerance — discard silently
        if fire_at < now - tolerance:
            logger.info(
                f"[calendar_reminder] Discarding missed window for: "
                f"{entry.get('event_title')} (offset {entry.get('offset_minutes')}m, "
                f"fire_at was {fire_at.isoformat()})"
            )
            entry["fired"] = True
            changed = True
            continue

        # Not yet time to fire
        if fire_at > now:
            continue

        # Due — mark fired immediately and queue for notification
        entry["fired"] = True
        changed = True
        to_fire.append(entry)

    # Flush fired state to disk before doing anything slow
    if changed:
        cutoff = now - timedelta(hours=2)
        settings["calendar_notify"]["pending_notifications"] = [
            n for n in pending
            if not n.get("fired")
            or ((fa := _parse_datetime(n.get("fire_at", ""))) is not None and fa >= cutoff)
        ]
        save_settings(settings)

    # ── Pass 2: Send Slack messages and voice alerts for all due entries ───────
    # These run after the state is already committed, so retries or concurrent
    # ticks cannot cause duplicate notifications.
    for entry in to_fire:
        offset     = entry["offset_minutes"]
        title      = entry.get("event_title", "(no title)")
        start_str  = entry.get("event_start", "")
        location   = entry.get("location", "")
        desc       = entry.get("description", "")

        start_dt   = _parse_datetime(start_str)
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
            lines.append(f"📝 {_format_description(desc)}")

        try:
            slack_client.chat_postMessage(
                channel=autonomous_channel_id,
                text="\n".join(lines),
                mrkdwn=True,
            )
            logger.info(f"[calendar_reminder] Notification fired: {title} ({time_label} before)")
        except Exception as e:
            logger.error(f"[calendar_reminder] Slack post failed for '{title}': {e}")

        # ── Voice alert (optional) ─────────────────────────────────────────────
        # voice_alerts: master on/off toggle
        # voice_llm: use LLM to generate a natural phrase (adds ~1 LLM call)
        if voice_alerts:
            try:
                from autonomous.jobs.voice_notifier import speak_calendar_alert
                speak_calendar_alert(title, offset, use_llm=voice_llm)
            except Exception as e:
                logger.warning(f"[calendar_reminder] Voice alert failed: {e}")


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


def _format_description(desc: str) -> str:
    """Convert HTML in calendar description to Slack-flavored markdown."""
    import re
    desc = re.sub(r'<br\s*/?>', '\n', desc, flags=re.IGNORECASE)
    desc = re.sub(r'</br>', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'<b>|</b>', '*', desc)
    desc = re.sub(r'<strong>|</strong>', '*', desc)
    desc = re.sub(r'<i>|</i>', '_', desc)
    desc = re.sub(r'<em>|</em>', '_', desc)
    desc = re.sub(r'<a\s+href="([^"]+)"[^>]*>([^<]*)</a>', r'<\1|\2>', desc)
    desc = re.sub(r'<[^>]+>', '', desc)
    desc = desc.replace('&nbsp;', ' ')
    desc = desc.replace('&amp;', '&')
    desc = desc.replace('&lt;', '<')
    desc = desc.replace('&gt;', '>')
    return desc[:1000]
