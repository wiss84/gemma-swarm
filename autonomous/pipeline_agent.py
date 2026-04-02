"""
Gemma Swarm — Autonomous Pipeline Agent
=========================================
Single Gemini flash lite LLM instance used exclusively by the autonomous pipeline.
No LangGraph, no state, no tools — just direct LLM calls with plain text prompts.

Used by: researcher, linkedin_drafter, daily_summary jobs.
NOT used by: email_watcher, calendar_reminder, inbox_checker, activity_logger.

Rate limit awareness:
- Gemini flash lite: 15 RPM, 250k TPM, 500/day
- Each call is tracked and throttled to stay under 15 RPM.
- A 5-second minimum gap is enforced between calls.
"""

import os
import time
import logging
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

MODEL_NAME      = "gemini-3.1-flash-lite-preview"
MIN_CALL_GAP_S  = 5   # seconds between calls — keeps well under 15 RPM

_llm            = None
_last_call_time = 0.0


def _get_llm() -> ChatGoogleGenerativeAI:
    global _llm
    if _llm is None:
        _llm = ChatGoogleGenerativeAI(
            model=MODEL_NAME,
            temperature=0.2,
            google_api_key=os.getenv("GOOGLE_API_KEY"),
        )
        logger.info(f"[autonomous/pipeline_agent] Initialized: {MODEL_NAME}")
    return _llm


def ask(prompt: str) -> str:
    """
    Send a plain text prompt to the autonomous LLM and return the response.
    Enforces a minimum gap between calls to respect rate limits.
    Sleeps automatically if called too quickly.
    """
    global _last_call_time

    # Enforce minimum gap between calls
    elapsed = time.time() - _last_call_time
    if elapsed < MIN_CALL_GAP_S:
        sleep_time = MIN_CALL_GAP_S - elapsed
        logger.info(f"[autonomous/pipeline_agent] Throttling — sleeping {sleep_time:.1f}s")
        time.sleep(sleep_time)

    llm = _get_llm()

    try:
        logger.info(f"[autonomous/pipeline_agent] Calling LLM ({len(prompt)} chars)")
        response         = llm.invoke([HumanMessage(content=prompt)])
        _last_call_time  = time.time()

        # Extract text — Gemini returns list content blocks
        content = response.content
        if isinstance(content, str):
            return content.strip()
        elif isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and "text" in first:
                return first["text"].strip()
            return str(first).strip()
        return str(content).strip()

    except Exception as e:
        _last_call_time = time.time()
        logger.error(f"[autonomous/pipeline_agent] LLM call failed: {e}")
        return f"[LLM error: {e}]"
