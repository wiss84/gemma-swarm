"""
Gemma Swarm — Rate Limit Callbacks
====================================
Countdown messages posted to Slack when a rate limit wait is triggered,
or when a server error (503/500) triggers a retry backoff.
Registered on all agent rate limiters at the start of each run,
cleared in the finally block when the run completes.
"""

import time
import logging
import threading

from agents.supervisor_agent       import get_supervisor_agent
from agents.memory_agent           import get_memory_agent

logger = logging.getLogger(__name__)


def _all_agents():
    return [
        get_supervisor_agent(),
        get_memory_agent(),
    ]


def make_wait_callback(client, channel: str, thread_ts: str):
    """Returns a callback that posts a countdown when rate limit wait is triggered."""
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


def make_retry_callback(client, channel: str, thread_ts: str):
    """Returns a callback that posts a countdown when a server-error retry is triggered."""
    def callback(model_name: str, seconds: float, exception: Exception):
        model_short = model_name.replace("gemma-3-", "").replace("-it", "")
        err_name    = type(exception).__name__
        text        = f"⚠️ Google {err_name} ({model_short}) — retrying in {int(seconds)}s..."
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
                            text=f"⚠️ Google {err_name} ({model_short}) — retrying in {remaining}s...",
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
            logger.warning(f"[slack] Could not post retry message: {e}")
    return callback


def register_wait_callbacks(client, channel: str, thread_ts: str):
    """Register rate-limit wait callbacks on all agents."""
    callback = make_wait_callback(client, channel, thread_ts)
    for agent in _all_agents():
        agent.rate_limiter.on_wait = callback


def register_retry_callbacks(client, channel: str, thread_ts: str):
    """Register server-error retry callbacks on all agents."""
    callback = make_retry_callback(client, channel, thread_ts)
    for agent in _all_agents():
        agent.rate_limiter.on_retry = callback


def clear_wait_callbacks():
    """Clear rate-limit wait callbacks from all agents after run completes."""
    for agent in _all_agents():
        agent.rate_limiter.on_wait = None


def clear_retry_callbacks():
    """Clear server-error retry callbacks from all agents after run completes."""
    for agent in _all_agents():
        agent.rate_limiter.on_retry = None
