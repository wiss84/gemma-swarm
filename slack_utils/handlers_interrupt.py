"""
Gemma Swarm — Interrupt Handlers
================================
Handles new messages arriving while the agent is already running.
Three options: combine with current task, fresh start, or queue.

Expected Flow:
- Queue: human_gate gets "rejected" → old task continues → then process queue
- Fresh Start: create NEW thread (messages[:-2]), cancel old, run fresh
- Combine: create NEW thread (ALL messages), combine messages, route through human_gate → start fresh
"""

import logging
import threading

from agents_utils.graph import get_graph
from agents_utils.config import INTERRUPT_BUTTON_TIMEOUT
from slack_utils.thread_state import get_thread_state

logger = logging.getLogger(__name__)


def build_interrupt_blocks(thread_ts: str, new_message: str) -> list:
    """Block Kit blocks for interrupt decision."""
    preview = new_message[:80] + "..." if len(new_message) > 80 else new_message
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"⚡ *New message received while I'm still working.*\n"
                    f"New message: _{preview}_\n\nWhat should I do?"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "🔀 Combine", "emoji": True},
                    "action_id": "interrupt_combine",
                    "value":     f"{thread_ts}|{new_message[:200]}",
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "🆕 Fresh Start", "emoji": True},
                    "style":     "primary",
                    "action_id": "interrupt_fresh",
                    "value":     f"{thread_ts}|{new_message[:200]}",
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "📋 Queue", "emoji": True},
                    "action_id": "interrupt_queue",
                    "value":     f"{thread_ts}|{new_message[:200]}",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"_No response in {INTERRUPT_BUTTON_TIMEOUT}s → will queue automatically_",
                }
            ],
        },
    ]


def register_interrupt_handlers(app, run_agent_fn):
    """
    Register interrupt button handlers on the Slack Bolt app.
    run_agent_fn is passed in to avoid circular imports with slack_bot.py.
    """

    @app.action("interrupt_combine")
    def handle_interrupt_combine(ack, body, client, say):
        """Handle Combine button click - Create new thread with combined message.
        
        Flow:
        1. Cancel the old task
        2. Get previous messages EXCEPT the last task (we'll replace it with combined)
        3. Combine original task + new interrupt message into ONE message
        4. Create NEW thread (overwrite registry)
        5. Start fresh with combined message on new thread
        """
        ack()
        value     = body["actions"][0]["value"]
        parts     = value.split("|", 1)
        thread_ts = parts[0]
        new_msg   = parts[1] if len(parts) > 1 else ""
        channel   = body["channel"]["id"]

        state = get_thread_state(thread_ts)
        
        # Store info for combine processing
        state.interrupt_action = "combine"
        state.interrupt_message = new_msg
        
        # Get old thread ID
        old_thread_id = state.active_thread_id
        state.old_thread_id = old_thread_id
        state.active_channel = channel
        
        # CANCEL the old task - we're replacing it with combined message
        # Save the cancel event reference before resetting
        if state.active:
            state.cancel_event.set()
        
        # Get previous messages EXCEPT the last task (we'll replace with combined)
        from langchain_core.messages import HumanMessage
        from agents_utils.config import LABEL
        
        graph = get_graph()
        config = {"configurable": {"thread_id": old_thread_id}}
        prev_messages = []
        last_task_msg = ""
        
        try:
            existing = graph.get_state(config)
            messages = existing.values.get("messages", []) if existing.values else []
            # Get previous messages EXCEPT last task (we'll create a new combined task)
            prev_messages = messages[:-1] if messages else []
            # Get the last human message (the task that was interrupted)
            for msg in reversed(messages):
                if isinstance(msg, HumanMessage):
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    if content.startswith(LABEL["human"]):
                        last_task_msg = content.replace(f"{LABEL['human']}", "").strip()
                        break
            logger.info(f"[slack] Combine: using {len(prev_messages)} previous messages, last task: {last_task_msg[:50]}")
        except Exception as e:
            logger.error(f"[slack] Could not get previous messages: {e}")
        
        # Combine the task message with the new interrupt message into ONE message
        combined_msg = f"{last_task_msg}, {new_msg}" if last_task_msg else new_msg

        # Use the SAME thread ID - just overwrite it with new messages
        # This keeps the thread_id the same, so resuming after restart works
        new_thread_id = old_thread_id
        
        # No need to save to registry - it's already saved for this thread_id
        
        # Update button message
        try:
            client.chat_update(
                channel=channel,
                ts=body["message"]["ts"],
                text="🔀 Starting fresh with combined message...",
                blocks=[],
            )
        except Exception:
            pass
        
        # CRITICAL: Signal human_gate to stop waiting and cancel the old task
        # This tells the old thread's human_gate that we've made a decision
        from nodes.human_gate import resolve_confirmation
        resolve_confirmation(thread_ts, "combine")
        
        # Clear interrupt state
        state.interrupt_pending = False
        state.interrupt_message = ""
        state.interrupt_action = ""
        
        # Wait a moment for old task to receive cancel signal
        import time as time_module
        time_module.sleep(0.5)
        
        # Reset cancel event AFTER old task has had time to see it
        state.cancel_event = threading.Event()
        
        # Start running the NEW thread with combined message
        try:
            compiled_graph = get_graph()
            
            # Build input state with previous messages (excludes old task) + combined message
            # This is like fresh start - we start from beginning with new task
            input_state = {
                "messages": prev_messages + [HumanMessage(content=f"{LABEL['human']}\n{combined_msg}")],
                "slack_thread_ts": thread_ts,
                "slack_channel": channel,
                "task_complete": False,
                "formatted_output": [],
                "is_interrupted": False,
            }
            
            new_config = {
                "configurable": {"thread_id": new_thread_id},
                "recursion_limit": 100,
            }
            
            # Update the checkpoint's original_task BEFORE streaming
            # This ensures input_router gets the correct original_task
            try:
                graph.update_state(
                    new_config,
                    {"original_task": combined_msg},
                    as_node="input_router",
                )
                logger.info(f"[slack] Combine: updated original_task to: {combined_msg[:50]}")
            except Exception as e:
                logger.error(f"[slack] Combine: could not update original_task: {e}")
            
            # Post status
            status_ts = ""
            from slack_utils.thread_state import post_status, STATUS_MESSAGES
            status_ts = post_status(client, channel, thread_ts, STATUS_MESSAGES.get("supervisor", "🧠 Supervisor is thinking...")) or ""
            state.status_ts = status_ts
            
            for chunk in compiled_graph.stream(input_state, new_config, stream_mode="updates"):
                # Check for cancellation
                if state.cancel_event.is_set():
                    logger.info(f"[slack] Combine cancelled: {thread_ts}")
                    break
                for node_name, node_output in chunk.items():
                    status_text = STATUS_MESSAGES.get(node_name)
                    if status_text and status_ts:
                        from slack_utils.thread_state import update_status
                        update_status(client, channel, status_ts, status_text)
                        
                    if node_name == "output_formatter":
                        formatted_output = node_output.get("formatted_output", [])
                        # Post the output
                        for chunk_text in formatted_output:
                            try:
                                client.chat_postMessage(
                                    channel=channel,
                                    thread_ts=thread_ts,
                                    text=chunk_text,
                                    mrkdwn=True,
                                )
                            except Exception as e:
                                logger.error(f"[slack] Could not post chunk: {e}")
                                
            # Clean up status
            if status_ts:
                from slack_utils.thread_state import delete_status
                delete_status(client, channel, status_ts)
                
        except Exception as e:
            logger.error(f"[slack] Combine error: {e}")
        
        logger.info(f"[slack] Combine: started new thread {new_thread_id} with combined message: {combined_msg[:50]}")


    @app.action("interrupt_fresh")
    def handle_interrupt_fresh(ack, body, client, say):
        """Handle Fresh Start button click - Create new thread with previous context (except last 2).
        
        Flow:
        1. Get previous messages EXCEPT last 2 (interrupted task + interrupt message)
        2. Cancel the old task
        3. Create NEW thread (overwrite registry)
        4. Signal human_gate with "fresh_start" to unblock it
        5. Start fresh on new thread with the new message
        """
        ack()
        value     = body["actions"][0]["value"]
        parts     = value.split("|", 1)
        thread_ts = parts[0]
        new_msg   = parts[1] if len(parts) > 1 else ""
        channel   = body["channel"]["id"]

        state = get_thread_state(thread_ts)
        
        # Store info for fresh start processing
        state.interrupt_action = "fresh"
        state.interrupt_message = new_msg
        
        # Get old thread ID
        old_thread_id = state.active_thread_id
        state.old_thread_id = old_thread_id
        state.active_channel = channel
        
        # Cancel the old task if still running
        if state.active:
            state.cancel_event.set()
        
        graph = get_graph()
        config = {"configurable": {"thread_id": old_thread_id}}
        prev_messages = []
        
        try:
            existing = graph.get_state(config)
            messages = existing.values.get("messages", []) if existing.values else []
            # Get all messages except the last 2 (the interrupted task, NOT the interrupt message)
            # This preserves all history including agent responses
            prev_messages = messages[:-2] if len(messages) >= 1 else []
            logger.info(f"[slack] Fresh start: using {len(prev_messages)} previous messages out of {len(messages)}")
        except Exception as e:
            logger.error(f"[slack] Could not get previous messages: {e}")
        
        # Use the SAME thread ID - just overwrite it with new messages
        # This keeps the thread_id the same, so resuming after restart works
        new_thread_id = old_thread_id
        
        # No need to save to registry - it's already saved for this thread_id
        
        # CRITICAL: Save the prev_messages to the NEW thread in LangGraph!
        # This is what was missing - we need to copy the history to the new thread
        try:
            new_graph_config = {"configurable": {"thread_id": new_thread_id}}
            graph.update_state(
                new_graph_config,
                {"messages": prev_messages},
                as_node="input_router",
            )
            logger.info(f"[slack] Fresh start: saved {len(prev_messages)} messages to new thread {new_thread_id}")
        except Exception as e:
            logger.error(f"[slack] Fresh start: could not save messages to new thread: {e}")
        
        # Update button message
        try:
            client.chat_update(
                channel=channel,
                ts=body["message"]["ts"],
                text="🆕 Fresh start! Send me a new message when ready.",
                blocks=[],
            )
        except Exception:
            pass
        
        # Signal human_gate to stop waiting
        from nodes.human_gate import resolve_confirmation
        resolve_confirmation(thread_ts, "fresh_start")
        
        # Clear interrupt state
        state.interrupt_pending = False
        state.interrupt_message = ""
        state.interrupt_action = ""
        state.cancel_event = threading.Event()
        
        logger.info(f"[slack] Fresh start: created new thread {new_thread_id} with {len(prev_messages)} messages, waiting for new message")


    @app.action("interrupt_queue")
    def handle_interrupt_queue(ack, body, client, say):
        """Handle Queue button click - Add to queue and let old task continue.
        
        Flow:
        1. Add message to queue
        2. Signal human_gate with "rejected" to unblock it
        3. Old task continues to completion
        4. After old task completes, queued messages are processed
        """
        ack()
        value     = body["actions"][0]["value"]
        parts     = value.split("|", 1)
        thread_ts = parts[0]
        new_msg   = parts[1] if len(parts) > 1 else ""
        channel   = body["channel"]["id"]

        state = get_thread_state(thread_ts)
        
        # Add to queue for later processing after old task completes
        if not hasattr(state, 'queued_messages'):
            state.queued_messages = []
        state.queued_messages.append(new_msg)
        
        # Clear interrupt state
        state.interrupt_pending = False
        state.interrupt_message = ""
        state.interrupt_action = ""
        
        # Signal human_gate with "rejected" to continue old task
        # In human_gate: rejected = continue to supervisor (old task continues)
        from nodes.human_gate import resolve_confirmation
        resolve_confirmation(thread_ts, "rejected")
        
        # Update button message
        try:
            client.chat_update(
                channel=channel,
                ts=body["message"]["ts"],
                text=f"📋 Queued ({len(state.queued_messages)} message(s)). Continuing with current task...",
                blocks=[],
            )
        except Exception:
            pass

        logger.info(f"[slack] Message queued for thread {thread_ts}. Queue size: {len(state.queued_messages)}")
