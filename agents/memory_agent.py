"""
Gemma Swarm — Memory Agent
============================
Compresses conversation history when context hits threshold.

Rolling compression:
- 1st compression: summarizes full messages list
- 2nd+ compression: summarizes previous summary + new messages
- Each compression replaces messages list with [MEMORY summary] + [latest HUMAN]

Model: gemma-3-4b-it (128k context, 8192 max output tokens)
"""

import logging
from langchain_core.messages import HumanMessage, AIMessage
from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from agents_utils.config import LABEL
from system_prompts.memory_prompt import get_prompt
from langchain_core.messages import HumanMessage as HM
from agents_utils.rate_limit_handler import RateLimitHandler

logger = logging.getLogger(__name__)


class MemoryAgent(BaseAgent):

    def __init__(self):
        super().__init__("memory")

    def get_system_prompt(self) -> str:
        # Base system prompt — no previous_summary here
        # previous_summary is injected per-call in compress()
        return get_prompt()

    def compress(self, messages: list, previous_summary: str = "") -> str:
        """
        Summarize the messages list into a single concise text.
        Builds a single focused prompt — no extra_context duplication.
        """
        conversation_text = _messages_to_text(messages)

        # Build one clean prompt combining instructions + previous summary + conversation
        full_prompt = get_prompt(previous_summary=previous_summary)
        full_prompt += f"\n\nConversation to summarize:\n\n{conversation_text}"

        # Call LLM directly — bypass base_agent.run() to avoid system prompt duplication
        llm_messages = [HM(content=full_prompt)]
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
            for label in LABEL.values():
                if content.startswith(label):
                    role    = label.strip("[]")
                    content = f"[{role}]: {content[len(label):].strip()}"
                    break
            if content:
                lines.append(content)
        elif isinstance(msg, AIMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content:
                lines.append(f"[AI]: {content}")
    return "\n\n".join(lines)


# ── Singleton ──────────────────────────────────────────────────────────────────

_memory_agent = None

def get_memory_agent() -> MemoryAgent:
    global _memory_agent
    if _memory_agent is None:
        _memory_agent = MemoryAgent()
    return _memory_agent


# ── Agent History Field Mapping ────────────────────────────────────────────────

AGENT_HISTORY_MAP = {
    "researcher":        ("researcher_history",        "researcher_context_summary"),
    "deep_researcher":   ("deep_researcher_history",   "deep_researcher_context_summary"),
    "email_composer":    ("email_history",             "email_context_summary"),
    "linkedin_composer": ("linkedin_history",          "linkedin_context_summary"),
}


def _compress_history(agent: MemoryAgent, history: list, previous_summary: str, label: str) -> tuple[list, str]:
    """
    Compress an agent's history list.
    Returns (compressed_messages, new_summary).
    """
    summary = agent.compress(
        messages=history,
        previous_summary=previous_summary,
    )
    compressed = [
        HumanMessage(
            content=f"{LABEL['memory']}\n"
                    f"Summary of {label} history:\n\n{summary}"
        )
    ]
    return compressed, summary


# ── LangGraph Node ─────────────────────────────────────────────────────────────

def memory_agent_node(state: AgentState) -> dict:
    """
    Compresses supervisor messages AND any agent histories that need it.
    All compressions happen in one pass before routing to guard_rails.
    """
    from agents_utils.config import CONTEXT_SUMMARIZE_THRESHOLD, MODEL_CONTEXT_WINDOWS, MODELS
    from agents_utils.memory import estimate_messages_tokens

    agent   = get_memory_agent()
    updates = {
        "active_agent": "memory",
        "next_node":    "guard_rails",
    }

    # ── 1. Compress supervisor messages if needed ──────────────────────────────
    messages         = state.get("messages", [])
    previous_summary = state.get("context_summary", "")
    supervisor_limit = MODEL_CONTEXT_WINDOWS[MODELS["supervisor"]]

    if (estimate_messages_tokens(messages) / supervisor_limit) >= CONTEXT_SUMMARIZE_THRESHOLD:
        logger.info(f"[memory_agent] Compressing supervisor: {len(messages)} messages.")

        latest_human = None
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if content.startswith(LABEL["human"]):
                    latest_human = msg
                    break

        summary = agent.compress(messages=messages, previous_summary=previous_summary)
        logger.info(f"[memory_agent] Supervisor summary: {len(summary)} chars.")

        compressed = [HumanMessage(
            content=f"{LABEL['memory']}\nSummary of conversation history:\n\n{summary}"
        )]
        if latest_human:
            compressed.append(latest_human)

        updates["messages"]        = compressed
        updates["context_summary"] = summary

    # ── 2. Compress individual agent histories if needed ──────────────────────
    for agent_name, (history_field, summary_field) in AGENT_HISTORY_MAP.items():
        history          = state.get(history_field, [])
        prev_summary     = state.get(summary_field, "")
        agent_model      = MODELS.get(agent_name, "gemma-3-12b-it")
        agent_limit      = MODEL_CONTEXT_WINDOWS.get(agent_model, 128000)

        if not history:
            continue

        if (estimate_messages_tokens(history) / agent_limit) >= CONTEXT_SUMMARIZE_THRESHOLD:
            logger.info(f"[memory_agent] Compressing {agent_name} history: {len(history)} messages.")
            compressed, summary = _compress_history(agent, history, prev_summary, agent_name)
            updates[history_field] = compressed
            updates[summary_field] = summary
            logger.info(f"[memory_agent] {agent_name} summary: {len(summary)} chars.")

    return updates
