"""
Gemma Swarm — Autonomous Daily Summary
========================================
Runs on app startup if no summary has been posted today.
Reads the last 24 hours of activity from the log sheet.
Asks LLM to format a clean daily summary.
Posts summary to autonomous Slack channel.
1 LLM call.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def run(slack_client, autonomous_channel_id: str):
    """
    Post a daily summary of autonomous activity to the Slack channel.
    The summary covers the previous 24 hours of logged activity.
    Skips if no activity was logged in that window.
    """
    from autonomous.settings import load_settings, save_settings
    from autonomous.jobs.activity_logger import read_recent_logs
    from autonomous import pipeline_agent

    settings = load_settings()

    # Read recent activity
    logs = read_recent_logs(hours=24)

    if not logs:
        logger.info("[daily_summary] No activity in last 24h — skipping summary.")
        # Still mark as done today so we don't keep checking
        settings["last_summary_date"] = datetime.now(timezone.utc).date().isoformat()
        save_settings(settings)
        return

    # Format log entries for LLM
    log_lines = "\n".join(
        f"- [{row['timestamp']}] {row['job']}: {row['description']} {row['status']}"
        for row in logs
    )

    # The summary covers the previous 24 hours, so label it with yesterday's date.
    # Example: if the app starts on Apr 8 at 9am, this summarises Apr 7 9am → Apr 8 9am,
    # so labelling it "Apr 7" is more accurate than "Apr 8".
    yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%B %d, %Y")

    prompt = f"""You are summarizing the last 24 hours of automated activity for the user of Gemma Swarm.

Here is the activity log:
{log_lines}

Write a short daily summary in EXACTLY this format — no extra text:

📊 *Autonomous Daily Summary — {yesterday_str}*

✉️ *Emails:* [summarize email watcher and inbox checker activity, or "Nothing new"]
📅 *Calendar:* [summarize calendar reminders, or "No events today"]
🔍 *Research:* [summarize research docs created, or "Not scheduled today"]
✍️ *LinkedIn:* [summarize LinkedIn drafts created, or "Not scheduled today"]

Keep each line to one sentence. Be direct and factual. Do not add extra sections."""

    summary = pipeline_agent.ask(prompt)

    if not summary or summary.startswith("[LLM error"):
        logger.error("[daily_summary] LLM call failed.")
        return

    # Post to Slack
    try:
        slack_client.chat_postMessage(
            channel=autonomous_channel_id,
            text=summary,
            mrkdwn=True,
        )
        logger.info("[daily_summary] Daily summary posted.")
    except Exception as e:
        logger.error(f"[daily_summary] Slack post failed: {e}")
        return

    # Mark summary as done today
    settings["last_summary_date"] = datetime.now(timezone.utc).date().isoformat()
    save_settings(settings)
