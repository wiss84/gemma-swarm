"""
Gemma Swarm — Base Agent
==========================
Shared base class for all agents.

Memory isolation per agent type:
- supervisor:     sees everything (human + planner + all agent results)
- planner:        sees only the latest human message
- researcher:     sees only supervisor messages (its assigned task)
- deep_researcher: same as researcher
- email_composer: same as researcher
- task_classifier: sees only the latest human message
- validator:      sees only supervisor messages
- memory:         sees supervisor messages only (what it needs to compress)
"""

import os
import json
import re
import logging
from abc import ABC, abstractmethod
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import BaseTool
from agents_utils.config import MODELS, LABEL, MAX_TOOL_ITERATIONS
from agents_utils.rate_limit_handler import RateLimitHandler, get_gemini_fallback_status
from agents_utils.json_parser import _extract_json

logger = logging.getLogger(__name__)

# Agents that use their own independent history
INDEPENDENT_HISTORY_AGENTS = {"researcher", "deep_researcher", "email_composer", "linkedin_composer", "gmail_agent", "calendar_agent", "docs_agent", "sheets_agent"}

# Agents that see only the latest human message
LATEST_HUMAN_ONLY_AGENTS = {"planner", "task_classifier"}

# Agents that see everything
FULL_CONTEXT_AGENTS = {"supervisor"}

# Map agent name to its history field in state
AGENT_HISTORY_FIELD = {
    "researcher":        "researcher_history",
    "deep_researcher":   "deep_researcher_history",
    "email_composer":    "email_history",
    "linkedin_composer": "linkedin_history",
    "gmail_agent":       "gmail_history",
    "calendar_agent":    "calendar_history",
    "docs_agent":        "docs_history",
    "sheets_agent":      "sheets_history",
}


def _filter_messages_for_agent(agent_name: str, messages: list, state: dict = None) -> list:
    """
    Return the correct message list for each agent type.
    
    - supervisor: full shared messages list
    - planner/classifier: only latest human message
    - researcher/email/linkedin/deep_researcher: their own independent history
    - others (memory, validator): full messages list
    """
    if agent_name in FULL_CONTEXT_AGENTS:
        return messages

    if agent_name in LATEST_HUMAN_ONLY_AGENTS:
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if not any(content.startswith(label) for label in LABEL.values()):
                    return [msg]
        return messages[-1:] if messages else []

    if agent_name in INDEPENDENT_HISTORY_AGENTS:
        # Use agent's own history from state
        if state:
            history_field = AGENT_HISTORY_FIELD.get(agent_name, "")
            agent_history = state.get(history_field, [])
            if agent_history:
                return agent_history
        # Fallback: extract only supervisor messages if no history yet
        filtered = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if content.startswith(LABEL["supervisor"]):
                    filtered.append(msg)
        return filtered[-1:] if filtered else []

    return messages


class BaseAgent(ABC):

    def __init__(self, agent_name: str):
        self.agent_name   = agent_name
        self.model_name   = MODELS[agent_name]
        self.fallback_used = False  # Track if this specific agent had to fallback
        
        # Check if Gemini fallback was already triggered (due to daily limit exhaustion)
        fallback_status = get_gemini_fallback_status()
        if fallback_status["fallback_used"] and self.model_name.startswith("gemini-"):
            self.model_name = "gemma-3-27b-it"
            self.fallback_used = True
            logger.warning(
                f"[{self.agent_name}] Using fallback model {self.model_name} "
                f"(Gemini daily limit exhausted)"
            )
        
        self.rate_limiter = RateLimitHandler(model_name=self.model_name)

        self.llm = ChatGoogleGenerativeAI(
            model=self.model_name,
            temperature=0.1,
            google_api_key=os.getenv("GOOGLE_API_KEY"),
        )

        self.tools: list[BaseTool] = []
        self.tool_registry: dict   = {}

        logger.info(f"[{self.agent_name}] Initialized with model {self.model_name}")

    @abstractmethod
    def get_system_prompt(self) -> str:
        pass

    def register_tools(self, tools: list[BaseTool]):
        self.tools         = tools
        self.tool_registry = {t.name: t for t in tools}

    def _build_tools_schema(self) -> list[dict]:
        return [
            {
                "name":        t.name,
                "description": t.description,
                "parameters":  t.args_schema.model_json_schema()
            }
            for t in self.tools
        ]

    def _extract_json(self, text: str) -> dict | None:
        return _extract_json(text)

    def _is_tool_call(self, parsed: dict) -> bool:
        return "tool" in parsed and "args" in parsed

    def _is_agent_response(self, parsed: dict) -> bool:
        return "response" in parsed

    def _execute_tool(self, tool_call: dict) -> str:
        tool_name = tool_call.get("tool")
        tool_args = tool_call.get("args", {})
        tool_fn   = self.tool_registry.get(tool_name)
        if tool_fn is None:
            return f"Error: Unknown tool '{tool_name}'"
        try:
            result = tool_fn.invoke(tool_args)
            return str(result)
        except Exception as e:
            return f"Tool error: {e}"

    def _extract_response_content(self, response) -> str:
        """
        Extract text content from LLM response.
        Handles both Gemini (list structure) and Gemma (string structure).
        """
        if isinstance(response.content, str):
            # Gemma models: content is directly a string
            return response.content
        elif isinstance(response.content, list) and len(response.content) > 0:
            # Gemini models: content is a list of content blocks
            # Extract text from first block
            first_block = response.content[0]
            if isinstance(first_block, dict) and 'text' in first_block:
                return first_block['text']
            else:
                # Fallback: try to convert to string
                return str(response.content)
        else:
            # Fallback for any other structure
            return str(response.content)

    def _call_llm(self, messages: list) -> AIMessage:
        input_tokens = RateLimitHandler.estimate_tokens(
            " ".join(
                m.content for m in messages
                if isinstance(m.content, str)
            )
        )
        # Pass input_tokens only for TPM check — Google's quota is on input tokens
        # Add 1000 buffer for expected output when recording after the call
        estimated = input_tokens + 1000

        response = self.rate_limiter.call_with_retry(
            self.llm.invoke,
            messages,
            estimated_tokens=estimated,
            input_tokens=input_tokens,
        )

        raw = self._extract_response_content(response)
        logger.debug(f"[{self.agent_name}] Response: {raw[:100]}")
        return response

    def run(
        self,
        messages: list,
        extra_context: str = "",
        max_tool_iterations: int = MAX_TOOL_ITERATIONS,
        state: dict = None,
    ) -> tuple[str, dict | None]:
        """
        Run the agent with filtered message history.
        Each agent uses its own independent history to avoid cross-contamination.
        """
        # Apply memory isolation filter — pass state for independent history agents
        filtered_messages = _filter_messages_for_agent(self.agent_name, messages, state=state)

        tools_schema_str = ""
        if self.tools:
            tools_schema_str = json.dumps(self._build_tools_schema(), indent=2)

        system_prompt = self.get_system_prompt()
        if tools_schema_str:
            system_prompt += f"\n\nAvailable tools:\n{tools_schema_str}"

        llm_messages = [HumanMessage(content=system_prompt)]

        if extra_context:
            llm_messages.append(HumanMessage(content=extra_context))

        llm_messages.extend(filtered_messages)

        for iteration in range(max_tool_iterations):
            response = self._call_llm(llm_messages)
            raw_text = self._extract_response_content(response)

            parsed = self._extract_json(raw_text)

            if parsed and self._is_tool_call(parsed):
                logger.info(f"[{self.agent_name}] Tool call: {parsed.get('tool')}")
                tool_result = self._execute_tool(parsed)
                logger.info(f"[{self.agent_name}] Tool result: {tool_result[:100]}")
                llm_messages.append(AIMessage(content=raw_text))
                llm_messages.append(
                    HumanMessage(
                        content=f"{LABEL['tool_result']}\n{tool_result}\n\n"
                                f"Now provide your response based on this result."
                    )
                )
                continue

            if parsed and self._is_agent_response(parsed):
                response_text = parsed.get("response", raw_text)
                logger.info(f"[{self.agent_name}] Structured response received.")
                return response_text, parsed

            logger.info(f"[{self.agent_name}] Plain text response.")
            return raw_text.strip(), None

        logger.warning(f"[{self.agent_name}] Max tool iterations reached.")
        return "Max tool iterations reached without a final response.", None
