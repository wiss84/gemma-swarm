"""
Gemma Swarm — Base Agent
"""

import os
import logging
from abc import ABC, abstractmethod
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from agents_utils.config import MODELS, MAX_TOOL_ITERATIONS
from agents_utils.rate_limit_handler import RateLimitHandler
from agents_utils.token_activity_tracker import record_token_event, estimate_tokens

logger = logging.getLogger(__name__)


class BaseAgent(ABC):

    def __init__(self, agent_name: str, model_name: str = None, status_callback=None):
        self.agent_name      = agent_name
        self.model_name      = model_name or MODELS[agent_name]
        self.status_callback = status_callback
        self.rate_limiter    = RateLimitHandler(model_name=self.model_name)
        self.llm             = ChatGoogleGenerativeAI(
            model=self.model_name,
            temperature=0.1,
            google_api_key=os.getenv("GOOGLE_API_KEY"),
        )
        self.tools: list[BaseTool] = []
        self.tool_registry: dict   = {}
        self.llm_with_tools        = None

    @abstractmethod
    def get_system_prompt(self) -> str:
        pass

    def register_tools(self, tools: list[BaseTool]):
        self.tools         = tools
        self.tool_registry = {t.name: t for t in tools}
        if tools:
            self.llm_with_tools = self.llm.bind_tools(tools)

    def _build_tools_schema(self) -> list[dict]:
        """
        Build JSON schemas for all registered tools.
        Used for context window size estimation.
        Returns a list of serializable dicts (one per tool).
        """
        schemas = []
        for tool in self.tools:
            try:
                if hasattr(tool, 'args_schema') and tool.args_schema:
                    schema = tool.args_schema.model_json_schema()
                elif hasattr(tool, 'schema'):
                    schema = tool.schema()
                else:
                    schema = {
                        "name": tool.name,
                        "description": getattr(tool, 'description', ''),
                        "parameters": {"type": "object", "properties": {}},
                    }
                schemas.append(schema)
            except Exception:
                schemas.append({
                    "name": tool.name,
                    "description": getattr(tool, 'description', ''),
                    "parameters": {"type": "object", "properties": {}},
                })
        return schemas

    def _execute_tool(self, tool_name: str, tool_args: dict) -> str:
        tool_fn = self.tool_registry.get(tool_name)
        if tool_fn is None:
            return f"Error: Unknown tool '{tool_name}'"
        try:
            return str(tool_fn.invoke(tool_args))
        except Exception as e:
            return f"Tool error: {e}"

    def _extract_response_content(self, response) -> str:
        content = response.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "thinking":
                        continue
                    parts.append(block.get("text", ""))
            return "".join(parts) if parts else str(content)
        return str(content)

    def _call_llm(self, messages: list, use_tools: bool = False) -> AIMessage:
        total_chars  = sum(len(m.content) if isinstance(m.content, str) else len(str(m.content)) for m in messages)
        input_tokens = max(1, int(total_chars / 4.0))
        estimated    = input_tokens
        llm_target   = self.llm_with_tools if use_tools else self.llm

        response = self.rate_limiter.call_with_retry(
            llm_target.invoke, messages,
            estimated_tokens=estimated,
            input_tokens=input_tokens,
        )

        raw          = self._extract_response_content(response)
        session_id   = getattr(self, "_current_session_id", "")
        project_name = getattr(self, "_current_project_name", "")

        if session_id:
            record_token_event(session_id=session_id, event_type="llm_input",
                               token_count=input_tokens, model=self.model_name, project_name=project_name)
            content = response.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type", "")
                        btext = block.get("text", "") or block.get("thinking", "")
                        if btext:
                            etype = "thinking" if btype == "thinking" else "llm_output"
                            record_token_event(session_id=session_id, event_type=etype,
                                               token_count=estimate_tokens(btext),
                                               model=self.model_name, project_name=project_name)
            elif raw:
                record_token_event(session_id=session_id, event_type="llm_output",
                                   token_count=estimate_tokens(raw),
                                   model=self.model_name, project_name=project_name)
        return response

    def _run_tool_loop(self, llm_messages: list, max_iterations: int, cancel_event=None) -> tuple[str, dict | None]:
        consecutive_empty      = 0
        task_complete_detected = False

        for _ in range(max_iterations):
            response   = self._call_llm(llm_messages, use_tools=True)
            raw_text   = self._extract_response_content(response)
            tool_calls = getattr(response, "tool_calls", None)

            if tool_calls:
                llm_messages.append(response)
                for tc in tool_calls:
                    tool_name = tc.get("name", "")
                    tool_args = tc.get("args", {})
                    tool_id   = tc.get("id", tool_name)

                    if cancel_event and cancel_event.is_set():
                        return "[cancelled]", None

                    if self.status_callback:
                        try:
                            self.status_callback(tool_name)
                        except Exception:
                            pass

                    tool_result = self._execute_tool(tool_name, tool_args)

                    session_id   = getattr(self, "_current_session_id", "")
                    project_name = getattr(self, "_current_project_name", "")
                    if session_id:
                        record_token_event(session_id=session_id, event_type="tool_input",
                                           token_count=estimate_tokens(str(tool_args)),
                                           model=self.model_name, project_name=project_name)
                        record_token_event(session_id=session_id, event_type="tool_output",
                                           token_count=estimate_tokens(tool_result),
                                           model=self.model_name, project_name=project_name)

                    if tool_name == "update_project_todo" and "__TASK_COMPLETE__" in tool_result:
                        task_complete_detected = True

                    llm_messages.append(ToolMessage(content=tool_result, tool_call_id=tool_id))
                consecutive_empty = 0
                continue

            if raw_text and raw_text.strip():
                parsed = {"task_complete": True} if task_complete_detected else None
                return raw_text.strip(), parsed

            consecutive_empty += 1
            if consecutive_empty <= 3:
                llm_messages.append(HumanMessage(content="Please continue."))
            else:
                break

        return "Max tool iterations reached without a final response.", None

    def run(self, messages: list, extra_context: str = "", max_tool_iterations: int = MAX_TOOL_ITERATIONS,
            state: dict = None, cancel_event=None) -> tuple[str, dict | None]:
        system_prompt = self.get_system_prompt()
        llm_messages  = [SystemMessage(content=system_prompt)]
        if extra_context:
            llm_messages.append(HumanMessage(content=extra_context))
        llm_messages.extend(messages)
        return self._run_tool_loop(llm_messages, max_tool_iterations, cancel_event=cancel_event)
