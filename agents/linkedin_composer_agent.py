"""
Gemma Swarm — LinkedIn Composer Agent
========================================
Two nodes:
  linkedin_composer_node — LLM writes the post, stores draft in state
  linkedin_send_node     — deterministic, calls LinkedIn API

Model: gemma-3n-e4b-it (128k context)
Routing: same as email (supervisor → composer → human_gate → send)
"""

import logging
from pathlib import Path
from langchain_core.messages import HumanMessage
from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from agents_utils.config import LABEL
from tools.linkedin_api import publish_linkedin_post, check_rate_limit

logger = logging.getLogger(__name__)


class LinkedInComposerAgent(BaseAgent):

    def __init__(self):
        super().__init__("linkedin_composer")

    def get_system_prompt(self) -> str:
        from system_prompts.linkedin_composer_prompt import get_prompt
        return get_prompt()

    def compose(
        self,
        task: str,
        messages: list,
        feedback: str = "",
        previous_draft: dict | None = None,
    ) -> dict | None:
        task_content = f"{LABEL['supervisor']}\n{task}"
        if previous_draft:
            previous_text = previous_draft.get("post_text", "")
            previous_media = previous_draft.get("media_filename", "")
            task_content += "\n\nPrevious draft for revision:\n"
            if previous_text:
                task_content += f"Post text: {previous_text}\n"
            if previous_media:
                task_content += f"Media filename: {previous_media}\n"
        if feedback:
            task_content += f"\n\nFeedback on previous draft: {feedback}\nPlease rewrite accordingly."

        response_text, parsed = self.run(
            messages=messages + [HumanMessage(content=task_content)],
            state=None
        )

        if parsed and "post_text" in parsed:
            return parsed

        logger.error(f"[linkedin_composer] Could not parse response: {response_text[:200]}")
        return None


# ── Singleton ──────────────────────────────────────────────────────────────────

_linkedin_composer_agent = None

def get_linkedin_composer_agent() -> LinkedInComposerAgent:
    global _linkedin_composer_agent
    if _linkedin_composer_agent is None:
        _linkedin_composer_agent = LinkedInComposerAgent()
    return _linkedin_composer_agent


# ── Composer Node ──────────────────────────────────────────────────────────────

def linkedin_composer_node(state: AgentState) -> dict:
    """
    LLM composes the LinkedIn post draft and stores it in state.
    Routes to human_gate for preview and approval.
    """
    agent            = get_linkedin_composer_agent()
    messages         = state.get("messages", [])
    current_subtask  = state.get("current_subtask", "")
    workspace_path   = state.get("workspace_path", "")
    linkedin_draft   = state.get("linkedin_draft", {})
    linkedin_history = list(state.get("linkedin_history", []))

    # Get feedback from previous rejection if any
    feedback = ""
    if linkedin_draft and linkedin_draft.get("feedback"):
        feedback = linkedin_draft["feedback"]

    previous_linkedin_draft = state.get("linkedin_draft", {})
    result = agent.compose(
        task=current_subtask,
        messages=linkedin_history,
        feedback=feedback,
        previous_draft=previous_linkedin_draft if previous_linkedin_draft else None,
    )

    if not result:
        # Fallback — route back to supervisor
        return {
            "active_agent":  "linkedin_composer",
            "next_node":     "supervisor",
            "error_message": "LinkedIn composer failed to produce a draft.",
            "messages": messages + [
                HumanMessage(
                    content=f"{LABEL['linkedin_composer']}\nFailed to compose post. Please try again."
                )
            ],
        }

    post_text      = result.get("post_text", "")
    media_filename = result.get("media_filename", "").strip()
    language       = result.get("language", "english")

    # Resolve media path if filename provided
    media_path = ""
    if media_filename and workspace_path:
        candidate = Path(workspace_path) / "linkedin_media" / "post_attachments" / media_filename
        if candidate.exists():
            media_path = str(candidate)
            logger.info(f"[linkedin_composer] Media found: {candidate}")
        else:
            logger.warning(f"[linkedin_composer] Media file not found: {candidate}")
            media_filename = f"{media_filename} ⚠️ (file not found in post_attachments)"

    new_draft = {
        "post_text":      post_text,
        "media_filename": media_filename,
        "media_path":     media_path,
        "language":       language,
        "feedback":       feedback,  # preserve feedback used for this rewrite
    }

    logger.info(f"[linkedin_composer] Draft ready. Media: {media_filename or 'none'}")

    # Save draft to post_drafts folder
    if workspace_path:
        try:
            import json
            from datetime import datetime
            drafts_dir = Path(workspace_path) / "linkedin_media" / "post_drafts"
            drafts_dir.mkdir(parents=True, exist_ok=True)
            timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
            draft_file = drafts_dir / f"draft_{timestamp}.json"
            draft_file.write_text(
                json.dumps(new_draft, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            logger.info(f"[linkedin_composer] Draft saved to {draft_file.name}")
        except Exception as e:
            logger.warning(f"[linkedin_composer] Could not save draft: {e}")

    # Update linkedin history
    task_msg   = HumanMessage(content=f"{LABEL['supervisor']}\n{current_subtask}")
    result_msg = HumanMessage(content=f"{LABEL['linkedin_composer']}\n{result.get('response', 'Post draft ready for review.')}")
    linkedin_history.extend([task_msg, result_msg])

    return {
        "active_agent":          "linkedin_composer",
        "linkedin_draft":        new_draft,
        "requires_confirmation": True,
        "next_node":             "human_gate",
        "linkedin_history":      linkedin_history,
        "messages": messages + [result_msg],
    }


# ── Send Node ──────────────────────────────────────────────────────────────────

def linkedin_send_node(state: AgentState) -> dict:
    """
    Deterministic node. Calls LinkedIn API to publish the approved post.
    No LLM involved.
    """
    messages       = state.get("messages", [])
    linkedin_draft = state.get("linkedin_draft", {})

    if not linkedin_draft:
        logger.error("[linkedin_send] No draft in state.")
        return {
            "active_agent": "linkedin_send",
            "next_node":    "supervisor",
            "messages": messages + [
                HumanMessage(
                    content=f"{LABEL['linkedin_composer']}\nNo LinkedIn draft found to publish."
                )
            ],
        }

    post_text  = linkedin_draft.get("post_text", "")
    media_path = linkedin_draft.get("media_path", "") or None

    # Check rate limit before attempting
    can_post, rate_msg = check_rate_limit()
    if not can_post:
        logger.warning(f"[linkedin_send] Rate limit reached: {rate_msg}")
        return {
            "active_agent":  "linkedin_send",
            "next_node":     "supervisor",
            "task_complete": True,
            "linkedin_draft": {},
            "messages": messages + [
                HumanMessage(content=f"{LABEL['linkedin_composer']}\n{rate_msg}")
            ],
        }

    # Build slack_post_fn so OAuth messages can reach the user if needed
    slack_channel    = state.get("slack_channel", "")
    slack_thread_ts  = state.get("slack_thread_ts", "")

    def slack_post_fn(msg: str):
        """Post a message to the current Slack thread (used for OAuth flow)."""
        try:
            from agents_utils.graph import _slack_client
            if _slack_client and slack_channel:
                _slack_client.chat_postMessage(
                    channel=slack_channel,
                    thread_ts=slack_thread_ts,
                    text=msg,
                )
        except Exception as e:
            logger.warning(f"[linkedin_send] Could not post to Slack: {e}")

    success, message = publish_linkedin_post(
        text=post_text,
        media_path=media_path,
        slack_post_fn=slack_post_fn,
    )

    result_label = "✅ Post published." if success else f"❌ Post failed: {message}"
    logger.info(f"[linkedin_send] {result_label}")

    return {
        "active_agent":   "linkedin_send",
        "next_node":      "supervisor",
        "task_complete":  True,   # Always complete — success or fail, don't retry
        "linkedin_draft": {},
        "messages": messages + [
            HumanMessage(content=f"{LABEL['linkedin_composer']}\n{message}")
        ],
    }
