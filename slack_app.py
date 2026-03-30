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
from agents_utils.state import default_state
from agents_utils.config import (
    LABEL,
    LANGGRAPH_RECURSION_LIMIT,
)

from slack_utils.thread_state       import (
    get_thread_state,
    get_threads_lock,
    get_threads_registry,
    load_registry_into_threads,
    STATUS_MESSAGES,
    post_status,
    delete_status,
    update_status,
)
from slack_utils.rate_callbacks     import register_wait_callbacks, clear_wait_callbacks
from slack_utils.handlers_workspace import build_workspace_blocks
from slack_utils.handlers_confirm   import register_confirm_handlers
from slack_utils.handlers_linkedin  import register_linkedin_handlers
from slack_utils.handlers_email     import register_email_handlers
from slack_utils.handlers_interrupt import register_interrupt_handlers
from slack_utils.handlers_files     import register_file_handlers
from slack_utils.handlers_workspace import register_workspace_handlers
from slack_utils.handlers_preferences import register_preferences_handlers
from slack_utils.handlers_google    import register_google_handlers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Slack App ──────────────────────────────────────────────────────────────────

app = App(token=os.environ["Bot_User_OAuth_Token"])
BOT_USER_ID = None



def _strip_slack_formatting(text: str) -> str:
    """Strip Slack auto-formatting: emails and URLs."""
    text = re.sub(r"<mailto:([^|>]+)\|[^>]+>", r"\1", text)
    text = re.sub(r"<(https?://[^|>]+)\|[^>]+>", r"\1", text)
    return text


# ── Core Worker ────────────────────────────────────────────────────────────────

def _run_agent(message: str, thread_ts: str, channel: str, client, say):
    """
    Core worker. Runs in a background thread.
    Streams graph, posts status updates, posts final response chunks.
    """
    state        = get_thread_state(thread_ts)
    state.active = True

    set_slack_client(client, lambda ts: ts)
    register_wait_callbacks(client, channel, thread_ts)


    compiled_graph = get_graph()
    # Use langgraph_thread_ts if set (resuming old project from new Slack thread)
    # Otherwise fall back to active_thread_id (normal flow)
    langgraph_thread = getattr(state, "langgraph_thread_ts", "") or state.active_thread_id
    config = {
        "configurable": {"thread_id": langgraph_thread},
        "recursion_limit": LANGGRAPH_RECURSION_LIMIT,
    }

    status_ts       = post_status(client, channel, thread_ts, STATUS_MESSAGES["supervisor"])
    state.status_ts = status_ts or ""

    formatted_output = []

    try:
        input_state = _build_input_state(
            message=message,
            state=state,
            slack_thread_ts=thread_ts,
            slack_channel=channel,
            compiled_graph=compiled_graph,
            config=config,
        )

        for chunk in compiled_graph.stream(input_state, config, stream_mode="updates"):

            # Check for interrupt - either cancel event OR interrupt_pending flag
            if state.cancel_event.is_set():
                logger.info(f"[slack] Cancelled: {thread_ts}")
                break
            
            # Check if interrupt message was received while running
            if getattr(state, 'interrupt_pending', False):
                logger.info(f"[slack] Interrupt detected while running: {thread_ts}")
                # Pause here and show interrupt buttons
                from nodes.human_gate import human_gate_node
                
                interrupt_msg = state.interrupt_message
                channel = state.active_channel or channel
                
                # Build interrupt state and call human_gate to show buttons
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
                    result = human_gate_node(interrupt_state, client=client)
                    decision = result.get("human_decision", "rejected")
                    logger.info(f"[slack] Interrupt decision: {decision}")
                    
                    # Handle the decision
                    if decision == "rejected":
                        # Queue - clear interrupt and continue
                        state.interrupt_pending = False
                        state.interrupt_message = ""
                        # Continue running...
                    elif decision == "combine":
                        # Combine - handled by button handler, cancel this task
                        state.cancel_event.set()
                        break
                    elif decision == "fresh_start":
                        # Fresh start - handled by button handler, cancel this task
                        state.cancel_event.set()
                        break
                except Exception as e:
                    logger.error(f"[slack] Error in interrupt handling: {e}")
                    state.interrupt_pending = False
                    state.interrupt_message = ""
            
            for node_name, node_output in chunk.items():
                status_text = STATUS_MESSAGES.get(node_name)
                if status_text and state.status_ts:
                    update_status(client, channel, state.status_ts, status_text)

                if node_name == "output_formatter":
                    formatted_output = node_output.get("formatted_output", [])

                logger.info(f"[slack] Node: {node_name}")

    except Exception as e:
        logger.error(f"[slack] Agent error: {e}")
        formatted_output = [f"❌ An error occurred: {e}"]

    finally:
        delete_status(client, channel, state.status_ts)
        clear_wait_callbacks()
        state.status_ts = ""
        state.active    = False

    if not state.cancel_event.is_set():
        if formatted_output:
            for chunk in formatted_output:
                try:
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=chunk,
                        mrkdwn=True,
                    )
                except Exception as e:
                    logger.error(f"[slack] Could not post chunk: {e}")
        else:
            try:
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="⚠️ I wasn't able to generate a response. Please hold on or try again in a moment.",
                )
            except Exception:
                pass

    # Check for interrupt action after task completes
    # Route to human_gate to show interrupt buttons
    if getattr(state, 'interrupt_pending', False) and not state.cancel_event.is_set():
        from nodes.human_gate import human_gate_node
        
        interrupt_msg = state.interrupt_message
        channel = state.active_channel or channel
        
        # Clear the is_interrupted flag in graph state
        try:
            graph = get_graph()
            config = {"configurable": {"thread_id": state.active_thread_id}}
            graph.update_state(config, {"is_interrupted": False}, )
        except Exception as e:
            logger.error(f"[slack] Could not clear interrupt flag: {e}")
        
        # Register for confirmation and show interrupt buttons via human_gate
        try:
            # Build state for human_gate
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
            
            # Call human_gate_node directly to show buttons and wait for decision
            result = human_gate_node(interrupt_state, client=client)
            
            # Get the decision from the result
            decision = result.get("human_decision", "rejected")
            
            logger.info(f"[slack] Interrupt decision: {decision}")
            
            # Handle based on decision
            if decision == "rejected":
                # Queue - continue with current task (which already completed)
                # Just process any queued messages
                queued_msgs = getattr(state, 'queued_messages', [])
                if queued_msgs and not state.cancel_event.is_set():
                    next_msg = queued_msgs.pop(0)
                    state.queued_messages = queued_msgs
                    state.interrupt_pending = False
                    state.interrupt_message = ""
                    state.cancel_event = threading.Event()
                    logger.info(f"[slack] Running queued message. Remaining: {len(state.queued_messages)}")
                    _run_agent(next_msg, thread_ts, channel, client, say)
                else:
                    state.interrupt_pending = False
                    state.interrupt_message = ""
                return
            elif decision == "combine":
                # Combine - the button handler already set up the new thread
                # Just need to start the new thread with combined message
                # The interrupt_action should be stored in state
                state.interrupt_pending = False
                state.interrupt_message = ""
                # The combine handler in handlers_interrupt.py handles starting new thread
                # But we need to trigger it - check if new thread was created
                new_thread_id = state.active_thread_id
                if new_thread_id and new_thread_id != getattr(state, 'old_thread_id', None):
                    # New thread created by button handler - run on it
                    logger.info(f"[slack] Combine: running on new thread {new_thread_id}")
                return
            elif decision == "fresh_start":
                # Fresh start - the button handler already created new thread and runs it
                # Just clear state
                state.interrupt_pending = False
                state.interrupt_message = ""
                return
            else:
                # Default - treat as queue
                state.interrupt_pending = False
                state.interrupt_message = ""
                
        except Exception as e:
            logger.error(f"[slack] Error showing interrupt buttons: {e}")
            # Fallback to queue behavior
            state.interrupt_pending = False
            state.interrupt_message = ""
        return

    # Process queued messages one by one
    queued_msgs = getattr(state, 'queued_messages', [])
    if queued_msgs and not state.cancel_event.is_set():
        # Get and remove the first message from queue
        next_msg = queued_msgs.pop(0)
        state.queued_messages = queued_msgs  # Update the list
        state.cancel_event = threading.Event()
        logger.info(f"[slack] Running queued message. Remaining: {len(state.queued_messages)}")
        _run_agent(next_msg, thread_ts, channel, client, say)


def _build_input_state(message, state, slack_thread_ts, slack_channel, compiled_graph, config):
    """Build input state for graph.stream()."""
    try:
        existing      = compiled_graph.get_state(config)
        existing_msgs = existing.values.get("messages", []) if existing.values else []
    except Exception:
        existing_msgs = []

    if existing_msgs:
        return {
            "messages": existing_msgs + [
                HumanMessage(content=f"{LABEL['human']}\n{message}")
            ],
            "slack_thread_ts":  slack_thread_ts,
            "slack_channel":    slack_channel,
            "task_complete":    False,
            "formatted_output": [],
        }
    else:
        s = default_state(
            original_task=message,
            workspace_path=state.workspace_path,
            project_name=state.project_name,
            slack_thread_ts=slack_thread_ts,
            slack_channel=slack_channel,
        )
        s["messages"] = [HumanMessage(content=f"{LABEL['human']}\n{message}")]
        return s



def _handle_message(message: str, thread_ts: str, channel: str, client, say):
    """Handle incoming message — interrupt or run directly."""
    state = get_thread_state(thread_ts)

    if state.active:
        # Set interrupt state in thread state - the running graph will check this
        # in its streaming loop and pause to show interrupt buttons
        state.interrupt_pending = True
        state.interrupt_message = message
        state.interrupt_action = ""
        state.active_channel = channel
        
        logger.info(f"[slack] Interrupt pending for thread {thread_ts}: {message[:50]}")
        return

    state.cancel_event = threading.Event()
    threading.Thread(
        target=_run_agent,
        args=(message, thread_ts, channel, client, say),
        daemon=True,
    ).start()


# ── Register Button Handlers ───────────────────────────────────────────────────

register_confirm_handlers(app)
register_linkedin_handlers(app)
register_email_handlers(app)
register_interrupt_handlers(app, _run_agent)
register_file_handlers(app, _run_agent)
register_workspace_handlers(app, _run_agent)
register_preferences_handlers(app, _run_agent)
register_google_handlers(app)





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

    if not state.workspace_path:
        state.pending_message = text
        state.pending_channel = channel
        try:
            result = client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="👋 Please select or create a workspace to get started.",
                blocks=build_workspace_blocks(thread_ts),
            )
            state.workspace_msg_ts = result.get("ts", "")
        except Exception as e:
            logger.error(f"[slack] Could not post workspace buttons: {e}")
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
    if not state.workspace_path:
        return

    _handle_message(_strip_slack_formatting(text), thread_ts, channel, client, say)


# ── Startup ────────────────────────────────────────────────────────────────────

def main():
    global BOT_USER_ID

    logger.info("[slack] Compiling graph...")
    get_graph()
    logger.info("[slack] Graph ready.")

    load_registry_into_threads()
    logger.info("[slack] Thread registry loaded.")

    try:
        result      = app.client.auth_test()
        BOT_USER_ID = result["user_id"]
        logger.info(f"[slack] Bot user ID: {BOT_USER_ID}")
    except Exception as e:
        logger.error(f"[slack] Could not get bot user ID: {e}")

    handler = SocketModeHandler(app, os.environ["agent_socket_token"])
    logger.info("[slack] Gemma Swarm is running ⚡")
    handler.start()


if __name__ == "__main__":
    main()
