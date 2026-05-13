"""
Gemma Swarm — Slack Bot
=========================
Core entry point. Handles Slack events and runs the LangGraph pipeline.

Responsibilities:
- Receive messages via Socket Mode
- Manage workspace selection per thread
- Post status updates as each node runs
- Delegate button handling to slack_utils sub-modules
"""

import os
import re
import logging
import threading
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from langchain_core.messages import HumanMessage

load_dotenv()

from agents_utils.graph import set_slack_client, get_graph
from agents.supervisor_agent import set_tool_status_callback, clear_tool_status_callback, get_supervisor_agent
from agents_utils.state import default_state
from agents_utils.config import (
    LANGGRAPH_RECURSION_LIMIT,
)

from slack_utils.thread_state       import (
    get_thread_state,
    get_threads_lock,
    get_threads_registry,
    load_registry_into_threads,
    load_coding_registry_into_threads,
    STATUS_MESSAGES,
    post_status,
    delete_status,
    update_status,
)
from slack_utils.rate_callbacks     import register_wait_callbacks, clear_wait_callbacks, register_retry_callbacks, clear_retry_callbacks
from slack_utils.handlers_workspace import build_workspace_blocks, build_graph_selector_blocks
from slack_utils.handlers_confirm   import register_confirm_handlers
from slack_utils.handlers_linkedin  import register_linkedin_handlers
from slack_utils.handlers_email     import register_email_handlers
from slack_utils.handlers_interrupt import register_interrupt_handlers
from slack_utils.handlers_files     import register_file_handlers
from slack_utils.handlers_workspace import register_workspace_handlers
from slack_utils.handlers_autonomous import register_autonomous_handlers
from slack_utils.handlers_preferences import register_preferences_handlers
from slack_utils.handlers_google    import register_google_handlers
from slack_utils.handlers_coding    import register_coding_handlers, run_coding_session_slack
from slack_utils.stream_manager     import StreamManager

logging.basicConfig(level=logging.INFO)
logging.getLogger("slack_bolt").setLevel(logging.WARNING)
logging.getLogger("agents_utils").setLevel(logging.WARNING)
logging.getLogger("autonomous").setLevel(logging.WARNING)
logging.getLogger("tools").setLevel(logging.WARNING)
logging.getLogger("coding_agent").setLevel(logging.WARNING)
logging.getLogger("nodes").setLevel(logging.WARNING)
logging.getLogger("agents").setLevel(logging.WARNING)
logging.getLogger("agents_utils").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ── Slack App ──────────────────────────────────────────────────────────────────

app = App(token=os.environ["Bot_User_OAuth_Token"])
BOT_USER_ID = None
TEAM_ID     = None  # set at startup via auth_test



def _strip_slack_formatting(text: str) -> str:
    """Strip Slack auto-formatting: emails and URLs."""
    text = re.sub(r"<mailto:([^|>]+)\|[^>]+>", r"\1", text)
    text = re.sub(r"<(https?://[^|>]+)\|[^>]+>", r"\1", text)
    return text


# ── Core Worker ────────────────────────────────────────────────────────────────

def _run_agent(message: str, thread_ts: str, channel: str, client, say, _is_retry: bool = False):
    """
    Core worker. Runs in a background thread.
    Opens a Slack stream for live thinking/tool cards via StreamManager.
    Posts the final response when done via existing output_formatter mechanism.
    """
    state        = get_thread_state(thread_ts)
    state.active = True

    set_slack_client(client, lambda ts: ts)
    register_wait_callbacks(client, channel, thread_ts)
    register_retry_callbacks(client, channel, thread_ts)

    compiled_graph = get_graph()
    langgraph_thread = getattr(state, "langgraph_thread_ts", "") or state.active_thread_id
    config = {
        "configurable": {"thread_id": langgraph_thread},
        "recursion_limit": LANGGRAPH_RECURSION_LIMIT,
    }

    # ── Stream manager: live thinking + tool cards ─────────────────────────
    stream_manager = StreamManager(client, channel, thread_ts, user_id=state.user_id)
    stream_manager.open()

    # Attach to supervisor so it pushes cards automatically
    supervisor = get_supervisor_agent()
    supervisor.stream_manager = stream_manager

    # Cycle the animated status text as each tool runs
    def tool_status_fn(tool_name: str):
        if tool_name == "thinking":
            stream_manager.set_status("🧠 Thinking...")
        else:
            readable = tool_name.replace("_", " ").title()
            stream_manager.set_status(f"🔧 {readable}")

    set_tool_status_callback(tool_status_fn)

    formatted_output = []

    try:
        input_state = _build_input_state(
            message=message,
            state=state,
            slack_thread_ts=thread_ts,
            slack_channel=channel,
            compiled_graph=compiled_graph,
            config=config,
            is_retry=_is_retry,
        )

        for chunk in compiled_graph.stream(input_state, config, stream_mode="updates"):

            if state.cancel_event.is_set():
                logger.info(f"[slack] Cancelled: {thread_ts}")
                break

            if getattr(state, 'interrupt_pending', False):
                logger.info(f"[slack] Interrupt detected while running: {thread_ts}")
                from nodes.human_gate import human_gate_node

                interrupt_msg = state.interrupt_message
                channel = state.active_channel or channel

                interrupt_state = {
                    "slack_thread_ts": thread_ts,
                    "slack_channel": channel,
                    "is_interrupted": True,
                    "interrupt_message": interrupt_msg,
                    "messages": [],
                    "pending_confirmation": "",
                    "email_draft": {},
                    "linkedin_draft": {},
                    "active_agent": "",
                }

                try:
                    result   = human_gate_node(interrupt_state, client=client)
                    decision = result.get("human_decision", "rejected")
                    logger.info(f"[slack] Interrupt decision: {decision}")

                    if decision == "rejected":
                        state.interrupt_pending = False
                        state.interrupt_message = ""
                    elif decision in ("combine", "fresh_start"):
                        state.cancel_event.set()
                        break
                except Exception as e:
                    logger.error(f"[slack] Error in interrupt handling: {e}")
                    state.interrupt_pending = False
                    state.interrupt_message = ""

            for node_name, node_output in chunk.items():
                if node_name == "output_formatter":
                    formatted_output = node_output.get("formatted_output", [])

    except Exception as e:
        logger.error(f"[slack] Agent error: {e}")

        state.last_error   = str(e)
        state.retry_config = config
        state.retry_message = message

        error_text    = f"❌ An error occurred: `{e}`"
        button_blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": error_text}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🔄 Continue", "emoji": True},
                        "action_id": "continue_after_error",
                        "value": thread_ts,
                        "style": "primary",
                    }
                ],
            },
        ]

        try:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                blocks=button_blocks, mrkdwn=True,
            )
        except Exception as post_err:
            logger.error(f"[slack] Could not post error with button: {post_err}")
            formatted_output = [f"❌ An error occurred: {e}"]

    finally:
        stream_manager.close()
        supervisor.stream_manager = None
        clear_tool_status_callback()
        clear_wait_callbacks()
        clear_retry_callbacks()
        state.status_ts = ""
        state.active    = False

    if not state.cancel_event.is_set():
        if formatted_output:
            pending_text: str | None = None

            for item in formatted_output:
                if isinstance(item, dict):
                    if pending_text is not None:
                        try:
                            client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                                    text=pending_text, mrkdwn=True)
                        except Exception as e:
                            logger.error(f"[slack] Could not post chunk: {e}")
                        pending_text = None
                    block_type    = item.get("type", "block")
                    text_fallback = item.get("text", {}).get("text", "")[:500].strip() or "See attached content."
                    logger.info(f"[slack] Posting {block_type} block")
                    try:
                        client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                                text=text_fallback, blocks=[item], mrkdwn=True)
                    except Exception as e:
                        logger.error(f"[slack] Could not post block: {e}")
                else:
                    if pending_text is not None:
                        is_code = "```" in pending_text
                        logger.info(f"[slack] Posting pending text: {len(pending_text)} chars, is_code={is_code}")
                        try:
                            client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                                    text=pending_text, mrkdwn=True)
                        except Exception as e:
                            logger.error(f"[slack] Could not post chunk: {e}")
                    pending_text = item

            if pending_text is not None:
                is_code = "```" in pending_text
                logger.info(f"[slack] Flushing pending text: {len(pending_text)} chars, is_code={is_code}, preview: {pending_text[:80]!r}")
                try:
                    client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                            text=pending_text, mrkdwn=True)
                except Exception as e:
                    logger.error(f"[slack] Could not post chunk: {e}")
        else:
            try:
                client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts,
                    text="⚠️ I wasn't able to generate a response. Please hold on or try again in a moment.",
                )
            except Exception:
                pass

    if getattr(state, 'interrupt_pending', False) and not state.cancel_event.is_set():
        from nodes.human_gate import human_gate_node

        interrupt_msg = state.interrupt_message
        channel       = state.active_channel or channel

        try:
            graph  = get_graph()
            config = {"configurable": {"thread_id": state.active_thread_id}}
            graph.update_state(config, {"is_interrupted": False})
        except Exception as e:
            logger.error(f"[slack] Could not clear interrupt flag: {e}")

        try:
            interrupt_state = {
                "slack_thread_ts": thread_ts, "slack_channel": channel,
                "is_interrupted": True, "interrupt_message": interrupt_msg,
                "messages": [], "pending_confirmation": "",
                "email_draft": {}, "linkedin_draft": {}, "active_agent": "",
            }
            result   = human_gate_node(interrupt_state, client=client)
            decision = result.get("human_decision", "rejected")
            logger.info(f"[slack] Interrupt decision: {decision}")

            if decision == "rejected":
                queued_msgs = getattr(state, 'queued_messages', [])
                if queued_msgs and not state.cancel_event.is_set():
                    next_msg = queued_msgs.pop(0)
                    state.queued_messages  = queued_msgs
                    state.interrupt_pending = False
                    state.interrupt_message = ""
                    state.cancel_event      = threading.Event()
                    logger.info(f"[slack] Running queued message. Remaining: {len(state.queued_messages)}")
                    _run_agent(next_msg, thread_ts, channel, client, say)
                else:
                    state.interrupt_pending = False
                    state.interrupt_message = ""
                return
            elif decision in ("combine", "fresh_start"):
                state.interrupt_pending = False
                state.interrupt_message = ""
                return
            else:
                state.interrupt_pending = False
                state.interrupt_message = ""
        except Exception as e:
            logger.error(f"[slack] Error showing interrupt buttons: {e}")
            state.interrupt_pending = False
            state.interrupt_message = ""
        return

    queued_msgs = getattr(state, 'queued_messages', [])
    if queued_msgs and not state.cancel_event.is_set():
        next_msg = queued_msgs.pop(0)
        state.queued_messages = queued_msgs
        state.cancel_event    = threading.Event()
        logger.info(f"[slack] Running queued message. Remaining: {len(state.queued_messages)}")
        _run_agent(next_msg, thread_ts, channel, client, say)


def _build_input_state(message, state, slack_thread_ts, slack_channel, compiled_graph, config, is_retry: bool = False):
    """Build input state for graph.stream().

    Args:
        is_retry: If True, returns the existing checkpoint state unchanged
                  (no new HumanMessage is appended). Used to resume after errors.
    """
    try:
        existing      = compiled_graph.get_state(config)
        existing_vals = existing.values if existing.values else {}
    except Exception:
        existing_vals = {}

    if existing_vals:
        if is_retry:
            return existing_vals
        else:
            msgs = existing_vals.get("messages", []) + [HumanMessage(content=message)]
            return {
                **existing_vals,
                "messages":         msgs,
                "slack_thread_ts":  slack_thread_ts,
                "slack_channel":    slack_channel,
                "task_complete":    False,
                "formatted_output": [],
                "loaded_toolset":   "",
            }
    else:
        s = default_state(
            original_task=message,
            workspace_path=state.workspace_path,
            project_name=state.project_name,
            slack_thread_ts=slack_thread_ts,
            slack_channel=slack_channel,
        )
        s["messages"] = [HumanMessage(content=message)]
        return s


def _handle_message(message: str, thread_ts: str, channel: str, client, say):
    """Handle incoming message — interrupt or run directly."""
    state = get_thread_state(thread_ts)

    state.retry_message = ""
    state.last_error    = ""
    state.retry_config  = None

    if state.active:
        state.interrupt_pending = True
        state.interrupt_message = message
        state.interrupt_action  = ""
        state.active_channel    = channel
        logger.info(f"[slack] Interrupt pending for thread {thread_ts}: {message[:50]}")
        return

    state.cancel_event = threading.Event()
    threading.Thread(
        target=_run_agent,
        args=(message, thread_ts, channel, client, say),
        daemon=True,
    ).start()


# ── Error Retry Handler ─────────────────────────────────────────────────────────

@app.action("continue_after_error")
def handle_continue_after_error(ack, body, client, say):
    """Resume the agent after a transient service error."""
    ack()
    thread_ts  = body["actions"][0]["value"]
    channel_id = body["channel"]["id"]

    state = get_thread_state(thread_ts)

    if state.active:
        try:
            client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text="⚠️ The agent is already running. Please wait for it to finish or send a new message.",
            )
        except Exception:
            pass
        return

    if not state.retry_message:
        try:
            client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text="✅ No pending error to retry.",
            )
        except Exception:
            pass
        return

    message             = state.retry_message
    state.retry_message = ""
    state.last_error    = ""
    state.retry_config  = None
    state.cancel_event  = threading.Event()

    threading.Thread(
        target=_run_agent,
        args=(message, thread_ts, channel_id, client, say, True),
        daemon=True,
    ).start()


# ── Register Button Handlers ───────────────────────────────────────────────────

register_confirm_handlers(app)
register_linkedin_handlers(app)
register_email_handlers(app)
register_interrupt_handlers(app, _run_agent)
register_file_handlers(app, _run_agent)
register_autonomous_handlers(app)
register_workspace_handlers(app, _run_agent)
register_preferences_handlers(app, _run_agent)
register_google_handlers(app)
register_coding_handlers(app)


# ── Slack Event Handlers ───────────────────────────────────────────────────────

@app.event("app_mention")
def handle_mention(event, client, say):
    global BOT_USER_ID
    channel   = event["channel"]
    thread_ts = event.get("thread_ts") or event["ts"]
    text      = event.get("text", "")

    if BOT_USER_ID:
        text = text.replace(f"<@{BOT_USER_ID}>", "").strip()
    text = _strip_slack_formatting(text)

    if not text:
        return

    state = get_thread_state(thread_ts)
    state.user_id = event.get("user", "")

    if not state.workspace_path:
        state.pending_message = text
        state.pending_channel = channel
        try:
            result = client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text="👋 Welcome to Gemma Swarm! What would you like to do?",
                blocks=build_graph_selector_blocks(thread_ts),
            )
            state.workspace_msg_ts = result.get("ts", "")
        except Exception as e:
            logger.error(f"[slack] Could not post graph selector: {e}")
        return

    if getattr(state, "coding_mode", False):
        if getattr(state, "coding_active", False):
            state.pending_message = text
            try:
                client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts,
                    text="⏳ Coding agent is busy. Your message has been queued.",
                )
            except Exception:
                pass
            return
        state.coding_active = True
        state.cancel_event  = threading.Event()
        threading.Thread(
            target=run_coding_session_slack,
            args=(text, thread_ts, channel, client, say,
                  state.workspace_path, state.project_name, thread_ts),
            daemon=True,
        ).start()
        return

    _handle_message(_strip_slack_formatting(text), thread_ts, channel, client, say)


@app.event("message")
def handle_message_event(event, client, say):
    global BOT_USER_ID

    if event.get("bot_id") or event.get("subtype"):
        return

    text    = event.get("text", "")
    channel = event["channel"]

    if BOT_USER_ID and f"<@{BOT_USER_ID}>" in text:
        return

    thread_ts = event.get("thread_ts")
    if not thread_ts:
        return

    with get_threads_lock():
        if thread_ts not in get_threads_registry():
            return

    state = get_thread_state(thread_ts)
    state.user_id = event.get("user", "")
    if not state.workspace_path:
        return

    if getattr(state, "coding_mode", False):
        if getattr(state, "coding_active", False):
            state.pending_message = _strip_slack_formatting(text)
            try:
                client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts,
                    text="⏳ Coding agent is busy. Your message has been queued.",
                )
            except Exception:
                pass
            return
        state.coding_active = True
        state.cancel_event  = threading.Event()
        threading.Thread(
            target=run_coding_session_slack,
            args=(_strip_slack_formatting(text), thread_ts, channel, client, say,
                  state.workspace_path, state.project_name, thread_ts),
            daemon=True,
        ).start()
        return

    _handle_message(_strip_slack_formatting(text), thread_ts, channel, client, say)


# ── Startup ────────────────────────────────────────────────────────────────────

def main():
    global BOT_USER_ID

    logger.info("[slack] Compiling graph...")
    get_graph()
    logger.info("[slack] Graph compiled successfully.")
    from autonomous.scheduler import start as start_autonomous_scheduler

    load_registry_into_threads()
    load_coding_registry_into_threads()

    try:
        result      = app.client.auth_test()
        BOT_USER_ID = result["user_id"]
    except Exception as e:
        logger.error(f"[slack] Could not get bot user ID: {e}")

    handler = SocketModeHandler(app, os.environ["agent_socket_token"])
    logger.info("[slack] Gemma Swarm is running ⚡")
    start_autonomous_scheduler(app.client)
    logger.info("[slack] Autonomous scheduler started.")
    handler.start()


if __name__ == "__main__":
    main()
