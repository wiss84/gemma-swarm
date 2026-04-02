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
from datetime import datetime

logger = logging.getLogger(__name__)


def run(slack_client, autonomous_channel_id: str):
    """
    Post a daily summary of autonomous activity to the Slack channel.
    Skips if no activity was logged in the last 24 hours.
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
        settings["last_summary_date"] = datetime.utcnow().date().isoformat()
        save_settings(settings)
        return

    # Format log entries for LLM
    log_lines = "\n".join(
        f"- [{row['timestamp']}] {row['job']}: {row['description']} {row['status']}"
        for row in logs
    )

    prompt = f"""You are summarizing today's automated activity for the user of Gemma Swarm.

Here is the activity log from the last 24 hours:
{log_lines}

Write a short daily summary in EXACTLY this format — no extra text:

📊 *Autonomous Daily Summary — {datetime.now().strftime("%B %d, %Y")}*

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
    settings["last_summary_date"] = datetime.utcnow().date().isoformat()
    save_settings(settings)
