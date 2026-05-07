"""
Gemma Swarm — Memory Agent (redesigned)
=========================================
Compresses conversation history when context hits threshold.

Rolling compression:
- 1st compression: summarizes full messages list
- 2nd+ compression: summarizes previous summary + new messages
- Each compression replaces messages list with [summary] + [latest user message]

No per-agent history fields — those were removed in the supervisor redesign.
"""

import logging
from langchain_core.messages import HumanMessage, AIMessage
from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from system_prompts.memory_prompt import get_prompt
from agents_utils.rate_limit_handler import RateLimitHandler

logger = logging.getLogger(__name__)


class MemoryAgent(BaseAgent):

    def __init__(self):
        super().__init__("memory")

    def get_system_prompt(self) -> str:
        return get_prompt()

    def compress(self, messages: list, previous_summary: str = "") -> str:
        """Summarize the messages list into a single concise text."""
        conversation_text = _messages_to_text(messages)
        full_prompt = get_prompt(previous_summary=previous_summary)
        full_prompt += f"\n\nConversation to summarize:\n\n{conversation_text}"

        llm_messages = [HumanMessage(content=full_prompt)]
        estimated    = RateLimitHandler.estimate_tokens(full_prompt) + 1000
        input_tokens = RateLimitHandler.estimate_tokens(full_prompt)

        response = self.rate_limiter.call_with_retry(
            self.llm.invoke,
            llm_messages,
            estimated_tokens=estimated,
            input_tokens=input_tokens,
        )

        result = response.content if isinstance(response.content, str) else str(response.content)
        return result.strip()


def _messages_to_text(messages: list) -> str:
    """Convert messages list to readable text for summarization."""
    lines = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content.strip():
                lines.append(f"[HUMAN]: {content.strip()}")
        elif isinstance(msg, AIMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content.strip():
                lines.append(f"[AI]: {content.strip()}")
    return "\n\n".join(lines)


# ── Singleton ──────────────────────────────────────────────────────────────────

_memory_agent = None


def get_memory_agent() -> MemoryAgent:
    global _memory_agent
    if _memory_agent is None:
        _memory_agent = MemoryAgent()
    return _memory_agent


# ── LangGraph Node ─────────────────────────────────────────────────────────────

def memory_agent_node(state: AgentState) -> dict:
    """Compresses supervisor messages when context hits threshold."""
    from agents_utils.config import CONTEXT_SUMMARIZE_THRESHOLD, MODEL_CONTEXT_WINDOWS, MODELS
    from agents_utils.memory import estimate_messages_tokens

    agent   = get_memory_agent()
    updates = {"active_agent": "memory", "next_node": "guard_rails"}

    messages         = state.get("messages", [])
    previous_summary = state.get("context_summary", "")
    supervisor_limit = MODEL_CONTEXT_WINDOWS[MODELS["supervisor"]]

    if (estimate_messages_tokens(messages) / supervisor_limit) >= CONTEXT_SUMMARIZE_THRESHOLD:
        logger.info(f"[memory_agent] Compressing: {len(messages)} messages.")

        # Preserve the last user message so the supervisor knows what was just asked
        latest_human = None
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                latest_human = msg
                break

        summary = agent.compress(messages=messages, previous_summary=previous_summary)
        logger.info(f"[memory_agent] Summary: {len(summary)} chars.")

        compressed = [HumanMessage(content=f"[Conversation summary]\n{summary}")]
        if latest_human and compressed[-1] is not latest_human:
            compressed.append(latest_human)

        updates["messages"]        = compressed
        updates["context_summary"] = summary

    return updates
