"""
Gemma Swarm — Autonomous Pipeline Agent
=========================================
Single LLM instance used exclusively by the autonomous pipeline.
No LangGraph, no state, no tools — just direct LLM calls with plain text prompts.

Used by: researcher, linkedin_drafter, daily_summary jobs.
NOT used by: email_watcher, calendar_reminder, inbox_checker, activity_logger.

Rate limiting:
- Uses RateLimitHandler for proper 429 handling, daily quota tracking,
  and proactive RPM throttling — same as all other agents.
- Additionally enforces a MIN_CALL_GAP_S floor between calls so concurrent
  autonomous jobs can't accidentally burst against the RPM limit.

Thinking blocks:
- Gemma 4 returns content as a list of {"type": "thinking"} and {"type": "text"}
  blocks. Thinking blocks are discarded; only text blocks are returned.
"""

import os
import time
import logging
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from agents_utils.rate_limit_handler import RateLimitHandler

logger = logging.getLogger(__name__)

MODEL_NAME     = "gemma-4-26b-a4b-it"
MIN_CALL_GAP_S = 5   # floor between calls — extra safety on top of RateLimitHandler

_llm            = None
_rate_limiter   = None
_last_call_time = 0.0


def _get_llm() -> tuple[ChatGoogleGenerativeAI, RateLimitHandler]:
    global _llm, _rate_limiter
    if _llm is None:
        _llm = ChatGoogleGenerativeAI(
            model=MODEL_NAME,
            temperature=0.2,
            google_api_key=os.getenv("GOOGLE_API_KEY"),
        )
        _rate_limiter = RateLimitHandler(model_name=MODEL_NAME)
        logger.info(f"[autonomous/pipeline_agent] Initialized: {MODEL_NAME}")
    return _llm, _rate_limiter


def _extract_text(response) -> str:
    """
    Extract plain text from an LLM response, discarding Gemma 4 thinking blocks.
    Mirrors the logic in BaseAgent._extract_response_content().
    """
    content = response.content

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list) and content:
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "thinking":
                    continue  # discard — internal reasoning, not part of the answer
                elif "text" in block:
                    # Older Gemini-style block with no 'type' key
                    text_parts.append(block["text"])
        if text_parts:
            return "".join(text_parts).strip()

    return str(content).strip()


def ask(prompt: str) -> str:
    """
    Send a plain text prompt to the autonomous LLM and return the response text.

    - Uses RateLimitHandler for 429 retries and daily quota tracking.
    - Enforces MIN_CALL_GAP_S floor between calls regardless of rate limits.
    - Discards Gemma 4 thinking blocks from the response.
    """
    global _last_call_time

    # Enforce minimum gap between calls (extra floor on top of rate limiter)
    elapsed = time.time() - _last_call_time
    if elapsed < MIN_CALL_GAP_S:
        sleep_time = MIN_CALL_GAP_S - elapsed
        logger.info(f"[autonomous/pipeline_agent] Throttling — sleeping {sleep_time:.1f}s")
        time.sleep(sleep_time)

    llm, limiter = _get_llm()

    estimated_tokens = RateLimitHandler.estimate_tokens(prompt) + 1500

    try:
        logger.info(f"[autonomous/pipeline_agent] Calling LLM ({len(prompt)} chars)")

        response = limiter.call_with_retry(
            llm.invoke,
            [HumanMessage(content=prompt)],
            estimated_tokens=estimated_tokens,
            input_tokens=RateLimitHandler.estimate_tokens(prompt),
        )

        _last_call_time = time.time()
        return _extract_text(response)

    except Exception as e:
        _last_call_time = time.time()
        logger.error(f"[autonomous/pipeline_agent] LLM call failed: {e}")
        return f"[LLM error: {e}]"
