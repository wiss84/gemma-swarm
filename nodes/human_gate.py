"""
Gemma Swarm — Human Gate Nodes
================================
Deterministic nodes. No LLM call.
Pauses the pipeline and posts Approve/Reject buttons to Slack.
Blocks until human responds or timeout occurs.
"""

import threading
import logging
from langchain_core.messages import HumanMessage
from agents_utils.state import AgentState
from agents_utils.config import HUMAN_CONFIRMATION_TIMEOUT, LABEL, INTERRUPT_BUTTON_TIMEOUT
from slack_utils.blocks import (
    build_interrupt_blocks,
    build_confirmation_blocks,
    build_email_preview_blocks,
    build_linkedin_preview_blocks,
    build_google_preview_blocks,
)

logger = logging.getLogger(__name__)

# Per-thread confirmation state
_pending_confirmations: dict = {}
_confirmations_lock = threading.Lock()


def register_confirmation(thread_ts: str) -> threading.Event:
    """Register a pending confirmation. Returns Event set when human responds."""
    event = threading.Event()
    with _confirmations_lock:
        _pending_confirmations[thread_ts] = {
            "event":    event,
            "decision": None,
        }
    return event


def resolve_confirmation(thread_ts: str, decision: str):
    """
    Called by Slack button/modal handler when human responds.
    decision format:
      - "approved"
      - "rejected"              (simple reject, no feedback)
      - "rejected: <feedback>"  (reject with feedback from modal)
    """
    with _confirmations_lock:
        if thread_ts in _pending_confirmations:
            _pending_confirmations[thread_ts]["decision"] = decision
            _pending_confirmations[thread_ts]["event"].set()
            logger.info(f"[human_gate] Resolved: {decision[:60]} for {thread_ts}")


def get_decision(thread_ts: str) -> str | None:
    with _confirmations_lock:
        entry = _pending_confirmations.get(thread_ts)
        return entry["decision"] if entry else None


def clear_confirmation(thread_ts: str):
    with _confirmations_lock:
        _pending_confirmations.pop(thread_ts, None)


def _handle_confirmation_wait(thread_ts: str, timeout: int) -> str:
    """Common logic to wait for human response or timeout."""
    event = register_confirmation(thread_ts)
    responded = event.wait(timeout=timeout)
    decision  = get_decision(thread_ts) if responded else "rejected"
    
    if not responded:
        logger.warning(f"[human_gate] Timeout after {timeout}s — defaulting to reject.")
    
    clear_confirmation(thread_ts)
    return decision


def interrupt_node(state: AgentState, client=None) -> dict:
    """
    Handles interrupts (new messages arriving during a task).
    Posts interrupt buttons and waits for decision.
    """
    thread_ts      = state.get("slack_thread_ts", "")
    channel        = state.get("slack_channel", "")
    interrupt_message = state.get("interrupt_message", "")

    if not thread_ts or not channel or client is None:
        logger.warning("[interrupt_node] No Slack context — auto-continuing.")
        return {
            "human_decision":        "rejected",
            "awaiting_human":        False,
            "is_interrupted":         False,
            "next_node":             "supervisor",
        }

    try:
        blocks = build_interrupt_blocks(thread_ts, interrupt_message)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="⚡ New message received while I'm working. What should I do?",
            blocks=blocks,
        )
        logger.info(f"[interrupt_node] Posted interrupt buttons to {thread_ts}")
    except Exception as e:
        logger.error(f"[interrupt_node] Could not post interrupt buttons: {e}")
        return {
            "human_decision":        "rejected",
            "awaiting_human":        False,
            "is_interrupted":         False,
            "next_node":             "supervisor",
            "error_message":         "Could not post interrupt buttons to Slack.",
        }

    decision = _handle_confirmation_wait(thread_ts, INTERRUPT_BUTTON_TIMEOUT)
    
    # Decision mapping for interrupts:
    # - "rejected" = queue (continue from where it was paused)
    # - "combine" or "fresh_start" = button handler is handling it, should NOT continue
    if decision == "rejected":
        next_node = "supervisor"
    else:
        next_node = "__interrupt_end__"
    
    logger.info(f"[interrupt_node] Decision: {decision[:60]} → {next_node}")
    
    return {
        "human_decision":        decision,
        "awaiting_human":        False,
        "is_interrupted":         False,
        "next_node":             next_node,
    }


def email_confirm_node(state: AgentState, client=None) -> dict:
    """Confirmation node for email drafts."""
    thread_ts  = state.get("slack_thread_ts", "")
    channel    = state.get("slack_channel", "")
    email_draft = state.get("email_draft", {})

    if not thread_ts or not channel or client is None:
        logger.warning("[email_confirm_node] No Slack context — auto-approving.")
        return {"human_decision": "approved", "awaiting_human": False, "next_node": "email_send"}

    try:
        blocks = build_email_preview_blocks(email_draft, thread_ts)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="📧 Email draft ready for review.",
            blocks=blocks,
        )
    except Exception as e:
        logger.error(f"[email_confirm_node] Could not post to Slack: {e}")
        return {"human_decision": "rejected", "awaiting_human": False, "next_node": "supervisor"}

    decision = _handle_confirmation_wait(thread_ts, HUMAN_CONFIRMATION_TIMEOUT)
    next_node = "email_send" if decision == "approved" else "supervisor"
    
    return _finalize_confirmation_state(state, decision, next_node)


def linkedin_confirm_node(state: AgentState, client=None) -> dict:
    """Confirmation node for LinkedIn posts."""
    thread_ts      = state.get("slack_thread_ts", "")
    channel        = state.get("slack_channel", "")
    linkedin_draft = state.get("linkedin_draft", {})

    if not thread_ts or not channel or client is None:
        logger.warning("[linkedin_confirm_node] No Slack context — auto-approving.")
        return {"human_decision": "approved", "awaiting_human": False, "next_node": "linkedin_send"}

    try:
        blocks = build_linkedin_preview_blocks(linkedin_draft, thread_ts)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="📝 LinkedIn post draft ready for review.",
            blocks=blocks,
        )
    except Exception as e:
        logger.error(f"[linkedin_confirm_node] Could not post to Slack: {e}")
        return {"human_decision": "rejected", "awaiting_human": False, "next_node": "supervisor"}

    decision = _handle_confirmation_wait(thread_ts, HUMAN_CONFIRMATION_TIMEOUT)
    next_node = "linkedin_send" if decision == "approved" else "supervisor"
    
    return _finalize_confirmation_state(state, decision, next_node)


def google_confirm_node(state: AgentState, client=None) -> dict:
    """Confirmation node for Google write actions."""
    thread_ts      = state.get("slack_thread_ts", "")
    channel        = state.get("slack_channel", "")
    pending_action = state.get("pending_confirmation", "Action requires your approval.")
    messages       = state.get("messages", [])
    active_agent   = state.get("active_agent", "")

    if not thread_ts or not channel or client is None:
        logger.warning("[google_confirm_node] No Slack context — auto-approving.")
        return {"human_decision": "approved", "awaiting_human": False, "next_node": "supervisor"}

    try:
        # Extract the result text from the most recent supervisor message
        google_result = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if content.startswith(LABEL["supervisor"]):
                    google_result = content[len(LABEL["supervisor"]):].strip()
                    break
        
        blocks = build_google_preview_blocks(google_result or pending_action, thread_ts)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="🔵 Google action ready for review.",
            blocks=blocks,
        )
    except Exception as e:
        logger.error(f"[google_confirm_node] Could not post to Slack: {e}")
        return {"human_decision": "rejected", "awaiting_human": False, "next_node": "supervisor"}

    decision = _handle_confirmation_wait(thread_ts, HUMAN_CONFIRMATION_TIMEOUT)
    next_node = "supervisor" if decision == "approved" else "supervisor" # Google always goes back to supervisor
    
    return _finalize_confirmation_state(state, decision, next_node)


def general_confirm_node(state: AgentState, client=None) -> dict:
    """Confirmation node for general actions."""
    thread_ts      = state.get("slack_thread_ts", "")
    channel        = state.get("slack_channel", "")
    pending_action = state.get("pending_confirmation", "Action requires your approval.")

    if not thread_ts or not channel or client is None:
        logger.warning("[general_confirm_node] No Slack context — auto-approving.")
        return {"human_decision": "approved", "awaiting_human": False, "next_node": "output_formatter"}

    try:
        blocks = build_confirmation_blocks(pending_action, thread_ts)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="⚠️ Human confirmation required.",
            blocks=blocks,
        )
    except Exception as e:
        logger.error(f"[general_confirm_node] Could not post to Slack: {e}")
        return {"human_decision": "rejected", "awaiting_human": False, "next_node": "supervisor"}

    decision = _handle_confirmation_wait(thread_ts, HUMAN_CONFIRMATION_TIMEOUT)
    
    # Route based on active agent
    active_agent = state.get("active_agent", "")
    if decision == "approved":
        if not active_agent:
            return {"human_decision": "approved", "awaiting_human": False, "next_node": "output_formatter"}
        return {"human_decision": "approved", "awaiting_human": False, "next_node": "supervisor"}
    
    return _finalize_confirmation_state(state, decision, "supervisor")


def _finalize_confirmation_state(state: AgentState, decision: str, next_node: str) -> dict:
    """Common logic to update state after a confirmation decision."""
    messages = state.get("messages", [])
    pending_action = state.get("pending_confirmation", "Action requires your approval.")
    
    # Extract feedback
    feedback = ""
    if decision.startswith("rejected:"):
        feedback = decision[len("rejected:"):].strip()

    # Inject feedback into drafts
    linkedin_draft = state.get("linkedin_draft", {})
    email_draft    = state.get("email_draft", {})
    if feedback:
        active_agent = state.get("active_agent", "")
        if active_agent == "linkedin_composer" and linkedin_draft:
            linkedin_draft = {**linkedin_draft, "feedback": feedback}
        elif active_agent == "email_composer" and email_draft:
            email_draft = {**email_draft, "feedback": feedback}

    return {
        "human_decision":              decision,
        "awaiting_human":              False,
        "google_requires_confirmation": False,
        "pending_confirmation":         "",
        "next_node":                   next_node,
        "active_agent":                state.get("active_agent", ""),
        "linkedin_draft":              linkedin_draft,
        "email_draft":                 email_draft,
        "messages": messages + [
            HumanMessage(
                content=f"{LABEL['human']}\nHuman decision: {decision}\nAction: {pending_action}"
            )
        ],
    }


def human_gate_node(state: AgentState, client=None) -> dict:
    """
    DEPRECATED: Use specialized confirm nodes and interrupt_node.
    Kept for backward compatibility during transition.
    """
    # This is now just a router to the new nodes
    is_interrupted = state.get("is_interrupted", False)
    if is_interrupted:
        return interrupt_node(state, client)
    
    active_agent = state.get("active_agent", "")
    if active_agent == "email_composer":
        return email_confirm_node(state, client)
    if active_agent == "linkedin_composer":
        return linkedin_confirm_node(state, client)
    if active_agent in {"calendar_agent", "docs_agent", "sheets_agent"}:
        return google_confirm_node(state, client)
    
    return general_confirm_node(state, client)
