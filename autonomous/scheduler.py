"""
Gemma Swarm — Autonomous Scheduler
=====================================
Background thread that runs every 60 seconds.
Checks which jobs are due and fires them in sub-threads.
Completely independent from the manual LangGraph pipeline.
Starts on app launch, stops when app stops.
"""

import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_scheduler_thread: threading.Thread | None = None
_slack_client                        = None
_stop_event                          = threading.Event()

# Track last poll times per job type
_last_email_watch_time    = 0.0
_last_inbox_check_time    = 0.0
_last_calendar_rescan_time = 0.0

# ── Research lock — prevents overlapping research runs ─────────────────────────
# is_research_due() reads last_run from disk, but last_run is only written AFTER
# the full research job completes (several minutes). Without this flag, the
# scheduler would fire a new research job on every 60s tick until the first
# run finishes and writes last_run to disk.
_research_running = False

CALENDAR_RESCAN_INTERVAL_S = 3600  # Rescan today's calendar every 1 hour


def start(slack_client):
    """
    Start the autonomous scheduler background thread.
    Called once from slack_app.py after the bot is ready.
    """
    global _scheduler_thread, _slack_client, _stop_event

    _slack_client = slack_client
    _stop_event   = threading.Event()

    _scheduler_thread = threading.Thread(
        target=_run_loop,
        name="autonomous_scheduler",
        daemon=True,
    )
    _scheduler_thread.start()
    logger.info("[scheduler] Autonomous scheduler started.")


def stop():
    """Stop the scheduler (called on app shutdown)."""
    _stop_event.set()
    logger.info("[scheduler] Autonomous scheduler stopped.")


def _run_loop():
    """Main scheduler loop. Runs every 60 seconds."""
    # On first start, run startup jobs immediately
    _run_startup_jobs()

    while not _stop_event.is_set():
        time.sleep(60)

        if _stop_event.is_set():
            break

        try:
            _tick()
        except Exception as e:
            logger.error(f"[scheduler] Tick error: {e}")


def _run_startup_jobs():
    """Jobs that run once when the app starts."""
    global _last_calendar_rescan_time

    from autonomous.settings import load_settings, is_summary_due

    settings = load_settings()

    if not settings.get("active", False):
        logger.info("[scheduler] Autonomous mode is OFF — skipping startup jobs.")
        return

    channel_id = settings.get("autonomous_channel_id", "")
    if not channel_id:
        logger.warning("[scheduler] No autonomous channel configured — skipping.")
        return

    logger.info("[scheduler] Running startup jobs...")

    # Daily summary — runs on startup if not posted today
    if is_summary_due(settings):
        _fire_job("daily_summary", channel_id)

    # Calendar reminder — always run on startup to schedule today's events
    if settings["calendar_notify"].get("enabled", True):
        _fire_job("calendar_reminder", channel_id)
        _last_calendar_rescan_time = time.time()


def _tick():
    """Called every 60 seconds. Checks which recurring jobs are due."""
    global _last_email_watch_time, _last_inbox_check_time, _last_calendar_rescan_time, _research_running

    from autonomous.settings import load_settings, is_research_due

    settings = load_settings()

    if not settings.get("active", False):
        return

    channel_id = settings.get("autonomous_channel_id", "")
    if not channel_id:
        return

    now = time.time()

    # ── Email watcher ──────────────────────────────────────────────────────────
    senders          = settings["email_watch"].get("senders", [])
    watch_interval_s = settings["email_watch"].get("poll_interval_minutes", 15) * 60

    if senders and (now - _last_email_watch_time) >= watch_interval_s:
        _last_email_watch_time = now
        _fire_job("email_watcher", channel_id)

    # ── Inbox checker ──────────────────────────────────────────────────────────
    inbox_enabled    = settings["inbox_check"].get("enabled", False)
    inbox_interval_s = settings["inbox_check"].get("poll_interval_minutes", 30) * 60

    if inbox_enabled and (now - _last_inbox_check_time) >= inbox_interval_s:
        _last_inbox_check_time = now
        _fire_job("inbox_checker", channel_id)

    # ── Calendar rescan — hourly to catch new events added during the day ──────
    cal_enabled = settings["calendar_notify"].get("enabled", True)
    if cal_enabled and (now - _last_calendar_rescan_time) >= CALENDAR_RESCAN_INTERVAL_S:
        _last_calendar_rescan_time = now
        _fire_job("calendar_reminder", channel_id)

    # ── Calendar notifications — fire any due alerts on every tick ─────────────
    if cal_enabled:
        _fire_job("calendar_fire_notifications", channel_id)

    # ── Research + LinkedIn drafts ─────────────────────────────────────────────
    # _research_running guards against overlapping runs: last_run is only written
    # to disk after the full job completes, so without this flag every tick would
    # fire a new research job until the first one finishes.
    topics = [t.strip() for t in settings["research"].get("topics", []) if t.strip()]
    if topics and is_research_due(settings) and not _research_running:
        _research_running = True
        _fire_job("research_and_linkedin", channel_id)


def _fire_job(job_name: str, channel_id: str):
    """Fire a job in its own sub-thread so it doesn't block the scheduler tick."""
    thread = threading.Thread(
        target=_run_job,
        args=(job_name, channel_id),
        name=f"autonomous_{job_name}",
        daemon=True,
    )
    thread.start()
    # logger.info(f"[scheduler] Fired job: {job_name}")


def _run_job(job_name: str, channel_id: str):
    """Execute a specific job by name."""
    global _research_running

    try:
        if job_name == "email_watcher":
            from autonomous.jobs.email_watcher import run
            run(_slack_client, channel_id)

        elif job_name == "inbox_checker":
            from autonomous.jobs.inbox_checker import run
            run(_slack_client, channel_id)

        elif job_name == "calendar_reminder":
            from autonomous.jobs.calendar_reminder import run
            run(_slack_client, channel_id)

        elif job_name == "calendar_fire_notifications":
            from autonomous.jobs.calendar_reminder import fire_due_notifications
            fire_due_notifications(_slack_client, channel_id)

        elif job_name == "research_and_linkedin":
            from autonomous.jobs.researcher import run as run_research
            from autonomous.jobs.linkedin_drafter import run as run_linkedin

            logger.info("[scheduler] Starting research job...")
            research_results = run_research(_slack_client, channel_id)

            if research_results:
                logger.info("[scheduler] Research done. Starting LinkedIn drafter...")
                run_linkedin(research_results, _slack_client, channel_id)
            else:
                logger.warning("[scheduler] Research returned no results — skipping LinkedIn drafter.")

        elif job_name == "daily_summary":
            from autonomous.jobs.daily_summary import run
            run(_slack_client, channel_id)

        else:
            logger.warning(f"[scheduler] Unknown job: {job_name}")

    except Exception as e:
        logger.error(f"[scheduler] Job '{job_name}' failed: {e}")

    finally:
        # Always release the research lock, even if the job crashed
        if job_name == "research_and_linkedin":
            _research_running = False
            logger.info("[scheduler] Research lock released.")
