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

Tool calling strategy (two code paths):
- Gemma 4 / Gemini: native function calling via llm.bind_tools().
  Tools are registered through the API's tools parameter (no prompt tokens consumed).
  Model returns AIMessage with tool_calls list; results fed back as ToolMessage.
- Gemma 3: text-JSON loop. Tools schema serialized into the system prompt as JSON.
  Model returns {"tool": "...", "args": {...}} text; results fed back as HumanMessage.
"""

import os
import json
import re
import logging
from abc import ABC, abstractmethod
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from agents_utils.config import MODELS, LABEL, MAX_TOOL_ITERATIONS
from agents_utils.rate_limit_handler import RateLimitHandler
from agents_utils.json_parser import _extract_json

logger = logging.getLogger(__name__)


# ── Model capability helpers ───────────────────────────────────────────────────

def _supports_system_message(model_name: str) -> bool:
    """
    Gemma 4 and Gemini support a dedicated system role (SystemMessage).
    Gemma 3 and earlier do not — system prompt goes in as the first HumanMessage.
    """
    return model_name.startswith("gemma-4-") or model_name.startswith("gemini-")


def _supports_native_tools(model_name: str) -> bool:
    """
    Gemma 4 and Gemini support native function calling via the API tools parameter.
    Gemma 3 uses the text-JSON loop instead.
    """
    return model_name.startswith("gemma-4-") or model_name.startswith("gemini-")


# ── Agent sets for message filtering ──────────────────────────────────────────

INDEPENDENT_HISTORY_AGENTS = {
    "researcher", "deep_researcher", "email_composer", "linkedin_composer",
    "gmail_agent", "calendar_agent", "docs_agent", "sheets_agent",
}
LATEST_HUMAN_ONLY_AGENTS = {"planner", "task_classifier"}
FULL_CONTEXT_AGENTS      = {"supervisor"}

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
    """Return the correct message slice for each agent type."""
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
        if state:
            history_field = AGENT_HISTORY_FIELD.get(agent_name, "")
            agent_history = state.get(history_field, [])
            if agent_history:
                return agent_history
        filtered = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if content.startswith(LABEL["supervisor"]):
                    filtered.append(msg)
        return filtered[-1:] if filtered else []

    return messages


# ── BaseAgent ─────────────────────────────────────────────────────────────────

class BaseAgent(ABC):

    def __init__(self, agent_name: str, model_name: str = None, status_callback=None):
        self.agent_name = agent_name
        self.model_name = model_name or MODELS[agent_name]
        self.status_callback = status_callback

        self.rate_limiter = RateLimitHandler(model_name=self.model_name)

        self.llm = ChatGoogleGenerativeAI(
            model=self.model_name,
            temperature=0.1,
            google_api_key=os.getenv("GOOGLE_API_KEY"),
        )

        self.tools: list[BaseTool] = []
        self.tool_registry: dict   = {}

        # llm_with_tools is set by register_tools() for Gemma 4 / Gemini agents.
        # For Gemma 3 agents it stays None and the text-JSON path is used instead.
        self.llm_with_tools = None

        # logger.info(f"[{self.agent_name}] Initialized: {self.model_name}")

    @abstractmethod
    def get_system_prompt(self) -> str:
        pass

    def register_tools(self, tools: list[BaseTool]):
        self.tools         = tools
        self.tool_registry = {t.name: t for t in tools}

        if _supports_native_tools(self.model_name) and tools:
            # Gemma 4 / Gemini: bind tools to the LLM via the API's native tools parameter.
            # This means tools are NOT injected into the prompt — zero prompt tokens spent.
            self.llm_with_tools = self.llm.bind_tools(tools)
            # logger.info(
            #     f"[{self.agent_name}] Native tool calling enabled "
            #     f"({len(tools)} tools bound via API)."
            # )
        else:
            # Gemma 3: text-JSON loop, tools schema injected into system prompt.
            self.llm_with_tools = None

    # ── Schema builder (Gemma 3 text-JSON path only) ──────────────────────────

    def _build_tools_schema(self) -> list[dict]:
        return [
            {
                "name":        t.name,
                "description": t.description,
                "parameters":  t.args_schema.model_json_schema()
            }
            for t in self.tools
        ]

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _extract_json(self, text: str) -> dict | None:
        return _extract_json(text)

    def _is_tool_call(self, parsed: dict) -> bool:
        return "tool" in parsed and "args" in parsed

    def _is_agent_response(self, parsed: dict) -> bool:
        return "response" in parsed

    def _execute_tool(self, tool_name: str, tool_args: dict) -> str:
        tool_fn = self.tool_registry.get(tool_name)
        if tool_fn is None:
            return f"Error: Unknown tool '{tool_name}'"
        try:
            result = tool_fn.invoke(tool_args)
            return str(result)
        except Exception as e:
            return f"Tool error: {e}"

    def _extract_response_content(self, response) -> str:
        """
        Extract plain text from an LLM response object.
        Handles Gemma 4 thinking blocks, Gemini content lists, and plain strings.
        """
        content = response.content

        if isinstance(content, str):
            return content

        if isinstance(content, list) and len(content) > 0:
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "thinking":
                        continue  # discard internal reasoning
                    elif "text" in block:
                        text_parts.append(block["text"])
            if text_parts:
                return "".join(text_parts)
            return str(content)

        return str(content)

    def _call_llm(self, messages: list, use_tools: bool = False) -> AIMessage:
        """
        Call the LLM and track tokens for rate limiting.
        use_tools=True selects llm_with_tools (Gemma 4 native path).
        """
        total_chars = sum(
            len(m.content) if isinstance(m.content, str) else len(str(m.content))
            for m in messages
        )
        # Native tool calling: tools aren't in the prompt so standard divisor is fine.
        # Text-JSON loop: tool schemas inflate the prompt, use tighter divisor.
        char_divisor = 4.0 if (use_tools or len(self.tools) <= 10) else 3.5
        input_tokens = max(1, int(total_chars / char_divisor))
        estimated    = input_tokens + 1500

        llm_target = self.llm_with_tools if use_tools else self.llm

        response = self.rate_limiter.call_with_retry(
            llm_target.invoke,
            messages,
            estimated_tokens=estimated,
            input_tokens=input_tokens,
        )

        raw = self._extract_response_content(response)
        logger.debug(f"[{self.agent_name}] Response: {raw[:100]}")
        return response

    # ── Native tool loop (Gemma 4 / Gemini) ──────────────────────────────────

    def _run_native_tool_loop(
        self,
        llm_messages: list,
        max_tool_iterations: int,
        cancel_event=None,
    ) -> tuple[str, dict | None]:
        """
        Tool loop for Gemma 4 / Gemini using native function calling.

        Conversation structure:
          SystemMessage(system_prompt)          ← system role, no prompt tokens
          HumanMessage(user task)               ← user turn
          AIMessage(tool_calls=[...])           ← model requests tool(s)
          ToolMessage(content, tool_call_id)    ← tool result(s)
          AIMessage(tool_calls=[...])           ← model requests more tools / final answer
          ...
          AIMessage(content="final response")   ← no tool_calls → we're done
        """
        consecutive_empty = 0
        task_complete_detected = False

        for iteration in range(max_tool_iterations):
            response = self._call_llm(llm_messages, use_tools=True)
            raw_text = self._extract_response_content(response)

            # Check for native tool_calls on the response object
            tool_calls = getattr(response, "tool_calls", None)

            if tool_calls:
                # Model issued one or more tool calls — execute each and feed results back
                llm_messages.append(response)  # append full AIMessage with tool_calls

                for tc in tool_calls:
                    tool_name = tc.get("name", "")
                    tool_args = tc.get("args", {})
                    tool_id   = tc.get("id", tool_name)

                    # Check cancel between tool executions
                    if cancel_event and cancel_event.is_set():
                        logger.info(f"[{self.agent_name}] Cancel detected mid-tool-loop, stopping.")
                        return "[cancelled]", None

                    if self.status_callback:
                        try:
                            self.status_callback(tool_name)
                        except Exception:
                            pass
                    # logger.info(f"[{self.agent_name}] Tool call (native): {tool_name}")
                    tool_result = self._execute_tool(tool_name, tool_args)
                    # logger.info(f"[{self.agent_name}] Tool result: {tool_result[:100]}")

                    # Detect task completion signal from the todo tool.
                    # Guard: only honour the sentinel when it comes from update_project_todo.
                    # Any other tool (read_files, execute_shell, etc.) may return source code
                    # or output that contains the sentinel string — those must be ignored.
                    if tool_name == "update_project_todo" and "__TASK_COMPLETE__" in tool_result:
                        task_complete_detected = True

                    llm_messages.append(
                        ToolMessage(content=tool_result, tool_call_id=tool_id)
                    )
                consecutive_empty = 0
                continue

            # No tool calls — model is producing a final response
            if raw_text and raw_text.strip():
                consecutive_empty = 0
                parsed = {"task_complete": True} if task_complete_detected else None
                return raw_text.strip(), parsed

            # Empty response — nudge once, then keep going
            consecutive_empty += 1
            if consecutive_empty <= 3:
                logger.warning(
                    f"[{self.agent_name}] Empty response on iteration {iteration} "
                    f"(consecutive: {consecutive_empty}). Nudging."
                )
                llm_messages.append(HumanMessage(content="Please continue."))
            else:
                logger.error(
                    f"[{self.agent_name}] {consecutive_empty} consecutive empty responses. Stopping."
                )
                break

        logger.warning(f"[{self.agent_name}] Native tool loop: max iterations reached.")
        return "Max tool iterations reached without a final response.", None

    # ── Text-JSON tool loop (Gemma 3) ─────────────────────────────────────────

    def _run_text_json_tool_loop(
        self,
        llm_messages: list,
        max_tool_iterations: int,
    ) -> tuple[str, dict | None]:
        """
        Tool loop for Gemma 3 using the text-JSON pattern.
        The model returns {"tool": "...", "args": {...}} or {"response": "..."}.
        Tool results are fed back as HumanMessage.
        """
        consecutive_empty = 0

        for iteration in range(max_tool_iterations):
            response = self._call_llm(llm_messages, use_tools=False)
            raw_text = self._extract_response_content(response)

            parsed = self._extract_json(raw_text)

            if parsed and self._is_tool_call(parsed):
                tool_name = parsed.get("tool")
                tool_args = parsed.get("args", {})
                if self.status_callback:
                    try:
                        self.status_callback(tool_name)
                    except Exception:
                        pass
                # logger.info(f"[{self.agent_name}] Tool call (text-JSON): {tool_name}")
                tool_result = self._execute_tool(tool_name, tool_args)
                # logger.info(f"[{self.agent_name}] Tool result: {tool_result[:100]}")
                llm_messages.append(AIMessage(content=raw_text))
                llm_messages.append(
                    HumanMessage(
                        content=f"{LABEL['tool_result']}\n{tool_result}\n\n"
                                f"Now continue to the next step, or provide your response "
                                f"based on this result, if there are no next steps."
                    )
                )
                consecutive_empty = 0
                continue

            if parsed and self._is_agent_response(parsed):
                # Always return parsed so callers get routing fields even when
                # response is intentionally empty (supervisor MODE A dispatch:
                # response="", next_node="deep_researcher", requires_deep_research=True).
                # The empty-response guard must NOT swallow a valid routing dict.
                response_text = parsed.get("response", "")
                return response_text.strip(), parsed

            # No valid JSON at all — check for plain-text response
            if raw_text and raw_text.strip():
                consecutive_empty = 0
                return raw_text.strip(), None

            # Truly empty raw_text — model returned nothing
            consecutive_empty += 1
            if consecutive_empty <= 3:
                logger.warning(
                    f"[{self.agent_name}] Empty response on iteration {iteration} "
                    f"(consecutive: {consecutive_empty}). Nudging."
                )
                llm_messages.append(HumanMessage(content="Please continue."))
            else:
                logger.error(
                    f"[{self.agent_name}] {consecutive_empty} consecutive empty responses. Stopping."
                )
                break

        logger.warning(f"[{self.agent_name}] Text-JSON tool loop: max iterations reached.")
        return "Max tool iterations reached without a final response.", None

    # ── Public run() ─────────────────────────────────────────────────────────

    def run(
        self,
        messages: list,
        extra_context: str = "",
        max_tool_iterations: int = MAX_TOOL_ITERATIONS,
        state: dict = None,
        cancel_event=None,
    ) -> tuple[str, dict | None]:
        """
        Run the agent. Selects native tool loop (Gemma 4/Gemini) or
        text-JSON loop (Gemma 3) based on model family.
        """
        filtered_messages = _filter_messages_for_agent(self.agent_name, messages, state=state)

        system_prompt = self.get_system_prompt()

        use_native = _supports_native_tools(self.model_name) and bool(self.llm_with_tools)

        if not use_native:
            # Gemma 3: inject tools schema into the system prompt as text
            if self.tools:
                tools_schema_str = json.dumps(self._build_tools_schema(), indent=2)
                system_prompt += f"\n\nAvailable tools:\n{tools_schema_str}"

        # Build initial message list
        if _supports_system_message(self.model_name):
            llm_messages = [SystemMessage(content=system_prompt)]
        else:
            llm_messages = [HumanMessage(content=system_prompt)]

        if extra_context:
            llm_messages.append(HumanMessage(content=extra_context))

        llm_messages.extend(filtered_messages)

        # Route to the appropriate tool loop
        if use_native:
            return self._run_native_tool_loop(llm_messages, max_tool_iterations, cancel_event=cancel_event)
        else:
            return self._run_text_json_tool_loop(llm_messages, max_tool_iterations)
