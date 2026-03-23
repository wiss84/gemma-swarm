"""
Gemma Swarm — Human Gate Node
================================
Deterministic node. No LLM call.
Pauses the pipeline and posts Approve/Reject buttons to Slack.
Blocks until human responds or timeout occurs.

Used for:
- Email approval (with reject → feedback modal → recompose)
- File deletion requests
- Agent escalations
"""

import threading
import logging
from langchain_core.messages import HumanMessage
from agents_utils.state import AgentState
from agents_utils.config import HUMAN_CONFIRMATION_TIMEOUT, LABEL, INTERRUPT_BUTTON_TIMEOUT

# Import interrupt blocks from handlers_interrupt
# build_interrupt_blocks is defined in handlers_interrupt.py but we build blocks inline here
# so we just need to ensure the function exists
def _get_interrupt_blocks(thread_ts: str, interrupt_message: str) -> list:
    """Build interrupt decision blocks inline (same as in handlers_interrupt.py)."""
    preview = interrupt_message[:80] + "..." if len(interrupt_message) > 80 else interrupt_message
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⚡ *New message received while working on a task.*\nNew message: _{preview}_\n\nWhat should I do?",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "🔀 Combine", "emoji": True},
                    "action_id": "interrupt_combine",
                    "value":     f"{thread_ts}|{interrupt_message[:200]}",
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "🆕 Fresh Start", "emoji": True},
                    "style":     "primary",
                    "action_id": "interrupt_fresh",
                    "value":     f"{thread_ts}|{interrupt_message[:200]}",
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "📋 Queue", "emoji": True},
                    "action_id": "interrupt_queue",
                    "value":     f"{thread_ts}|{interrupt_message[:200]}",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_No response in {INTERRUPT_BUTTON_TIMEOUT // 60} minutes → will queue automatically_"}
            ],
        },
    ]

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


def build_confirmation_blocks(pending_action: str, thread_ts: str) -> list:
    """Standard Approve/Reject buttons for general confirmations."""
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"⚠️ *Human confirmation required.*\n\n{pending_action}"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✅ Approve", "emoji": True},
                    "style":     "primary",
                    "action_id": "confirm_approve",
                    "value":     thread_ts,
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "❌ Reject", "emoji": True},
                    "style":     "danger",
                    "action_id": "confirm_reject",
                    "value":     thread_ts,
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_No response in {HUMAN_CONFIRMATION_TIMEOUT // 60} minutes → defaults to Reject_"}
            ],
        },
    ]


def build_email_preview_blocks(draft: dict, thread_ts: str) -> list:
    """
    Email preview with Approve and Reject (opens feedback modal) buttons.
    Reject opens a modal so the user can type feedback.
    """
    to_list  = ", ".join(draft.get("to", []))
    subject  = draft.get("subject", "")
    body     = draft.get("rendered_body", draft.get("message", ""))
    language = draft.get("language", "english")
    layout   = draft.get("layout", "official")

    # Truncate body preview for Slack block (3000 char limit per block)
    body_preview = body[:2800] + "..." if len(body) > 2800 else body

    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "📧 *Email Draft — Please Review*"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*To:*\n{to_list}"},
                {"type": "mrkdwn", "text": f"*Subject:*\n{subject}"},
                {"type": "mrkdwn", "text": f"*Language:*\n{language}"},
                {"type": "mrkdwn", "text": f"*Layout:*\n{layout}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Message:*\n```{body_preview}```"},
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✅ Send Email", "emoji": True},
                    "style":     "primary",
                    "action_id": "email_approve",
                    "value":     thread_ts,
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✏️ Reject & Give Feedback", "emoji": True},
                    "style":     "danger",
                    "action_id": "email_reject_feedback",
                    "value":     thread_ts,
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_No response in {HUMAN_CONFIRMATION_TIMEOUT // 60} minutes → defaults to Reject_"}
            ],
        },
    ]


def build_feedback_modal(thread_ts: str) -> dict:
    """
    Slack modal that opens when user clicks 'Reject & Give Feedback'.
    The modal submit triggers resolve_confirmation with the feedback text.
    """
    return {
        "type":            "modal",
        "callback_id":     "email_feedback_modal",
        "private_metadata": thread_ts,
        "title":           {"type": "plain_text", "text": "Email Feedback"},
        "submit":          {"type": "plain_text", "text": "Submit"},
        "close":           {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type":    "input",
                "block_id": "feedback_block",
                "label":   {"type": "plain_text", "text": "What should be changed?"},
                "element": {
                    "type":        "plain_text_input",
                    "action_id":   "feedback_input",
                    "multiline":   True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "e.g. Make it more formal, shorten the second paragraph, translate to French..."
                    },
                },
            }
        ],
    }


def build_linkedin_preview_blocks(draft: dict, thread_ts: str) -> list:
    """
    LinkedIn post preview with Approve and Reject (feedback modal) buttons.
    """
    post_text      = draft.get("post_text", "")
    media_filename = draft.get("media_filename", "")
    language       = draft.get("language", "english")

    # Truncate preview for Slack (3000 char limit per block)
    preview = post_text[:2800] + "..." if len(post_text) > 2800 else post_text

    media_line = f"\n📎 *Attached:* `{media_filename}`" if media_filename else ""
    lang_line  = f" · Language: {language}" if language != "english" else ""

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*📝 LinkedIn Post — Please Review*{lang_line}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{preview}{media_line}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✅ Approve & Post", "emoji": True},
                    "style":     "primary",
                    "action_id": "linkedin_approve",
                    "value":     thread_ts,
                },
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "✏️ Reject & Give Feedback", "emoji": True},
                    "style":     "danger",
                    "action_id": "linkedin_reject_feedback",
                    "value":     thread_ts,
                },
            ],
        },
    ]
    return blocks


def build_linkedin_feedback_modal(thread_ts: str) -> dict:
    """Slack modal for LinkedIn post feedback."""
    return {
        "type":             "modal",
        "callback_id":      "linkedin_feedback_modal",
        "private_metadata": thread_ts,
        "title":            {"type": "plain_text", "text": "Post Feedback"},
        "submit":           {"type": "plain_text", "text": "Send Feedback"},
        "close":            {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type":     "input",
                "block_id": "feedback_block",
                "label":    {"type": "plain_text", "text": "What should be changed?"},
                "element":  {
                    "type":      "plain_text_input",
                    "action_id": "feedback_input",
                    "multiline": True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "e.g. Make it shorter, add more hashtags, change the tone...",
                    },
                },
            }
        ],
    }


def human_gate_node(state: AgentState, client=None) -> dict:
    """
    LangGraph node for human-in-the-loop confirmation.
    Detects whether this is an email approval, general confirmation, or interrupt
    and posts the appropriate Slack blocks.
    """
    thread_ts      = state.get("slack_thread_ts", "")
    channel        = state.get("slack_channel", "")
    pending_action = state.get("pending_confirmation", "Action requires your approval.")
    messages       = state.get("messages", [])
    email_draft    = state.get("email_draft", {})
    active_agent   = state.get("active_agent", "")
    is_interrupted = state.get("is_interrupted", False)
    interrupt_message = state.get("interrupt_message", "")

    # No Slack context — auto-approve for local testing
    if not thread_ts or not channel or client is None:
        logger.warning("[human_gate] No Slack context — auto-approving.")
        # For interrupts, still need to handle state
        if is_interrupted:
            return {
                "human_decision":        "rejected",  # Default for interrupt = continue
                "awaiting_human":        False,
                "requires_confirmation": False,
                "is_interrupted":         False,  # Clear interrupt flag
                "next_node":             "supervisor",
                "active_agent":          active_agent,
                "linkedin_draft":        state.get("linkedin_draft", {}),
                "email_draft":           state.get("email_draft", {}),
            }
        return {
            "human_decision":        "approved",
            "awaiting_human":        False,
            "requires_confirmation": False,
            "next_node":             _resolve_next_node(state, "approved"),
            "active_agent":          active_agent,
            "linkedin_draft":        state.get("linkedin_draft", {}),
            "email_draft":           state.get("email_draft", {}),
        }

    event = register_confirmation(thread_ts)

    linkedin_draft = state.get("linkedin_draft", {})

    # Check if this is an interrupt situation
    if is_interrupted:
        # This is an interrupt - show interrupt buttons
        try:
            blocks = _get_interrupt_blocks(thread_ts, interrupt_message)
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="⚡ New message received while I'm working. What should I do?",
                blocks=blocks,
            )
            logger.info(f"[human_gate] Posted interrupt buttons to {thread_ts}")
        except Exception as e:
            logger.error(f"[human_gate] Could not post interrupt buttons: {e}")
            clear_confirmation(thread_ts)
            return {
                "human_decision":        "rejected",
                "awaiting_human":        False,
                "requires_confirmation": False,
                "is_interrupted":         False,
                "next_node":             "supervisor",
                "error_message":         "Could not post interrupt buttons to Slack.",
            }
    else:
        # Normal human confirmation (email/linkedin approval)
        is_linkedin = active_agent == "linkedin_composer" and bool(linkedin_draft)
        is_email    = active_agent == "email_composer" and bool(email_draft)

        try:
            if is_linkedin:
                blocks = build_linkedin_preview_blocks(linkedin_draft, thread_ts)
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="📝 LinkedIn post draft ready for review.",
                    blocks=blocks,
                )
            elif is_email:
                blocks = build_email_preview_blocks(email_draft, thread_ts)
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="📧 Email draft ready for review.",
                    blocks=blocks,
                )
            else:
                blocks = build_confirmation_blocks(pending_action, thread_ts)
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="⚠️ Human confirmation required.",
                    blocks=blocks,
                )
            logger.info(f"[human_gate] Posted {'linkedin preview' if is_linkedin else 'email preview' if is_email else 'confirmation'} to {thread_ts}")
        except Exception as e:
            logger.error(f"[human_gate] Could not post to Slack: {e}")
            clear_confirmation(thread_ts)
            return {
                "human_decision":        "rejected",
                "awaiting_human":        False,
                "requires_confirmation": False,
                "next_node":             "supervisor",
                "error_message":         "Could not post confirmation to Slack.",
            }

    # Block until human responds or timeout
    # Use interrupt timeout if interrupted, otherwise use human confirmation timeout
    timeout = INTERRUPT_BUTTON_TIMEOUT if is_interrupted else HUMAN_CONFIRMATION_TIMEOUT
    responded = event.wait(timeout=timeout)
    decision  = get_decision(thread_ts) if responded else "rejected"

    if not responded:
        logger.warning("[human_gate] Timeout — defaulting to reject.")
        # For interrupt timeout, default to "queue" behavior (continue)
        if is_interrupted:
            decision = "rejected"  # rejected = continue to supervisor = queue behavior

    clear_confirmation(thread_ts)
    
    # Handle routing based on interrupt status
    if is_interrupted:
        # Clear the interrupt flag
        is_interrupted = False
        
        # Decision mapping for interrupts:
        # - "rejected" = queue (continue from where it was paused)
        # - "combine" or "fresh_start" = button handler is handling it, should NOT continue
        if decision == "rejected":
            # Queue - continue to supervisor (old task continues)
            next_node = "supervisor"
        else:
            # Combine or fresh start - button handler is cancelling this task
            # Don't continue to supervisor - just end here
            # The button handler will start a new thread
            next_node = "__interrupt_end__"  # Special marker to not continue
        
        logger.info(f"[human_gate] Interrupt decision: {decision[:60]} → {next_node}")
    else:
        next_node = _resolve_next_node(state, decision)
        logger.info(f"[human_gate] Decision: {decision[:60]} → {next_node}")

    # Extract feedback from decision string "rejected: <feedback>"
    feedback = ""
    if decision.startswith("rejected:"):
        feedback = decision[len("rejected:"):].strip()

    # Inject feedback into draft so composer can use it on rewrite
    linkedin_draft = state.get("linkedin_draft", {})
    email_draft    = state.get("email_draft", {})
    if feedback:
        if state.get("active_agent") == "linkedin_composer" and linkedin_draft:
            linkedin_draft = {**linkedin_draft, "feedback": feedback}
        elif state.get("active_agent") == "email_composer" and email_draft:
            email_draft = {**email_draft, "feedback": feedback}

    # Build return state
    return_state = {
        "human_decision":        decision,
        "awaiting_human":        False,
        "requires_confirmation": False,
        "pending_confirmation":  "",
        "next_node":             next_node,
        "active_agent":          state.get("active_agent", ""),
        "linkedin_draft":        linkedin_draft,
        "email_draft":           email_draft,
        "messages": messages + [
            HumanMessage(
                content=f"{LABEL['human']}\nHuman decision: {decision}\nAction: {pending_action}"
            )
        ],
    }
    
    # Clear interrupt flag if it was set
    if state.get("is_interrupted", False):
        return_state["is_interrupted"] = False
    
    return return_state


def _resolve_next_node(state: AgentState, decision: str) -> str:
    """Route after human decision."""
    active_agent = state.get("active_agent", "")

    if decision == "approved":
        if active_agent == "email_composer":
            return "email_send"
        if active_agent == "linkedin_composer":
            return "linkedin_send"
        return "supervisor"

    # Rejected — always back to supervisor with feedback in decision string
    return "supervisor"
