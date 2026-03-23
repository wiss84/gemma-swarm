"""
Gemma Swarm — Rate Limit Callbacks
=====================================
Countdown messages posted to Slack when a rate limit wait is triggered.
Registered on all agent rate limiters at the start of each run,
cleared in the finally block when the run completes.
"""

import time
import logging
import threading

from agents.task_classifier_agent import get_task_classifier_agent
from agents.planner_agent          import get_planner_agent
from agents.supervisor_agent       import get_supervisor_agent
from agents.researcher_agent       import get_researcher_agent
from agents.deep_researcher_agent  import get_deep_researcher_agent
from agents.email_composer_agent   import get_email_composer_agent
from agents.linkedin_composer_agent import get_linkedin_composer_agent
from agents.memory_agent           import get_memory_agent

logger = logging.getLogger(__name__)


def _all_agents():
    return [
        get_task_classifier_agent(),
        get_planner_agent(),
        get_supervisor_agent(),
        get_researcher_agent(),
        get_deep_researcher_agent(),
        get_email_composer_agent(),
        get_linkedin_composer_agent(),
        get_memory_agent(),
    ]


def make_wait_callback(client, channel: str, thread_ts: str):
    """Returns a callback that posts a countdown when rate limit is hit."""
    def callback(model_name: str, seconds: float):
        model_short = model_name.replace("gemma-3-", "").replace("-it", "")
        text        = f"⏳ Rate limit reached ({model_short}) — resuming in {int(seconds)}s..."
        try:
            result = client.chat_postMessage(
                channel=channel, thread_ts=thread_ts, text=text,
            )
            msg_ts = result["ts"]

            def update_and_delete():
                remaining = int(seconds)
                interval  = 15 if seconds > 30 else 5
                while remaining > interval:
                    time.sleep(interval)
                    remaining -= interval
                    try:
                        client.chat_update(
                            channel=channel, ts=msg_ts,
                            text=f"⏳ Rate limit reached ({model_short}) — resuming in {remaining}s...",
                        )
                    except Exception:
                        break
                time.sleep(max(0, remaining))
                try:
                    client.chat_delete(channel=channel, ts=msg_ts)
                except Exception:
                    pass

            threading.Thread(target=update_and_delete, daemon=True).start()
        except Exception as e:
            logger.warning(f"[slack] Could not post rate limit message: {e}")
    return callback


def register_wait_callbacks(client, channel: str, thread_ts: str):
    """Register rate limit callbacks on all agents."""
    callback = make_wait_callback(client, channel, thread_ts)
    for agent in _all_agents():
        agent.rate_limiter.on_wait = callback


def clear_wait_callbacks():
    """Clear rate limit callbacks from all agents after run completes."""
    for agent in _all_agents():
        agent.rate_limiter.on_wait = None
