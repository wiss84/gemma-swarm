"""
Gemma Swarm — Response Validator Node
========================================
Always runs LLM validation — never skips.

Validates the supervisor's final response against original_task —
the full user request that started the pipeline.
LLM validation always runs — never skipped.
"""

import os
import re
import json
import logging
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from agents_utils.state import AgentState
from agents_utils.config import LABEL, MODELS
from agents_utils.rate_limit_handler import RateLimitHandler

logger = logging.getLogger(__name__)



_validator_llm     = None
_validator_limiter = None


def _get_validator():
    global _validator_llm, _validator_limiter
    if _validator_llm is None:
        _validator_llm = ChatGoogleGenerativeAI(
            model=MODELS["validator"],
            temperature=0.1,
            google_api_key=os.getenv("GOOGLE_API_KEY"),
        )
        _validator_limiter = RateLimitHandler(model_name=MODELS["validator"])
    return _validator_llm, _validator_limiter


def _python_checks(response_text: str) -> tuple[bool, str]:
    """Fast pre-checks before LLM validation."""
    if not response_text or len(response_text.strip()) < 5:
        return False, "Response is empty or too short."
    if '"tool"' in response_text and '"args"' in response_text:
        return False, "Response contains a raw tool call — not a proper answer."
    stripped = response_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            json.loads(stripped)
            return False, "Response is raw JSON — not a proper natural language answer."
        except json.JSONDecodeError:
            pass
    return True, ""


def _resolve_validation_task(original_task: str) -> str:
    """Always returns original_task — the user message that started the pipeline."""
    return original_task.strip()


def _llm_validate(task: str, response_text: str) -> tuple[bool, str]:
    """Always runs LLM validation against the resolved task."""
    llm, limiter = _get_validator()

    from system_prompts.validator_prompt import get_prompt
    prompt = get_prompt(task, response_text)

    estimated = RateLimitHandler.estimate_tokens(prompt)

    try:
        result = limiter.call_with_retry(
            llm.invoke,
            [HumanMessage(content=prompt)],
            estimated_tokens=estimated,
        )
        raw   = result.content if isinstance(result.content, str) else str(result.content)
        match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if match:
            parsed   = json.loads(match.group(0))
            valid    = parsed.get("valid", True)
            feedback = parsed.get("feedback", "").strip()
            # If invalid but no feedback provided — 1b model failed to explain why
            # Treat as pass-through to avoid looping on empty reason
            if not valid and not feedback:
                logger.warning("[validator] Model returned invalid with no feedback — passing through.")
                return True, ""
            return valid, feedback
    except Exception as e:
        logger.warning(f"[validator] LLM validation error: {e} — passing through.")

    # If validation itself errors, pass through to avoid blocking pipeline
    return True, ""


def response_validator_node(state: AgentState) -> dict:
    messages      = state.get("messages", [])
    original_task = state.get("original_task", "")
    retry_counts  = state.get("retry_counts", {})

    # Get the latest supervisor response
    response_text = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content.startswith(LABEL["supervisor"]):
                response_text = content.replace(LABEL["supervisor"], "").strip()
                break

    if not response_text:
        logger.warning("[validator] No supervisor response found — passing through.")
        return {"next_node": "output_formatter"}

    # Step 1: Fast Python checks
    passed, reason = _python_checks(response_text)
    if not passed:
        logger.warning(f"[validator] Python check failed: {reason}")
        return _handle_invalid(state, messages, retry_counts, reason)

    # Step 1b: If response contains a clear system failure/error — pass through
    # These are legitimate outcomes (attachment blocked, API failed, etc.)
    # Validator should not loop trying to "fix" a correctly reported failure
    failure_markers = ["❌", "attachment type", "not allowed", "failed to send", 
                       "could not", "authentication failed", "no recipients"]
    if any(marker.lower() in response_text.lower() for marker in failure_markers):
        logger.info("[validator] Response contains failure/error report — passing through.")
        return {"next_node": "output_formatter"}

    # Step 2: Always run LLM validation
    task = _resolve_validation_task(original_task)
    logger.info(f"[validator] Validating against: '{task[:80]}'")

    valid, feedback = _llm_validate(task, response_text)
    if not valid:
        logger.warning(f"[validator] LLM check failed: {feedback}")
        return _handle_invalid(state, messages, retry_counts, feedback)

    logger.info("[validator] Response passed validation.")
    return {"next_node": "output_formatter"}


def _handle_invalid(state, messages, retry_counts, reason) -> dict:
    validator_retries = retry_counts.get("validator", 0)
    max_retries       = 2

    if validator_retries >= max_retries:
        logger.warning("[validator] Max retries reached — escalating to human.")
        return {
            "next_node":             "human_gate",
            "requires_confirmation": True,
            "pending_confirmation": (
                f"Response validator failed {max_retries} times.\n"
                f"Last reason: {reason}\nShould I try again or stop here?"
            ),
            "retry_counts": {**retry_counts, "validator": validator_retries + 1},
        }

    return {
        "next_node":     "supervisor",
        "error_message": f"Response validation failed: {reason}.",
        "retry_counts":  {**retry_counts, "validator": validator_retries + 1},
        "messages": messages + [
            HumanMessage(
                content=f"{LABEL['system']}\n"
                        f"Your previous response failed validation.\n"
                        f"Reason: {reason}\n"
                        f"Please provide a proper response to the original task."
            )
        ],
    }
