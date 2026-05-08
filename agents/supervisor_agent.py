"""
Gemma Swarm — Supervisor Agent (redesigned)
=============================================
Pure tool-calling agent. Zero routing logic. Zero message labels.

Flow every turn:
  1. LLM called with [load_toolset] as the only tool.
  2. If LLM calls load_toolset("X"):
       - Feature gate checked.
       - CONFIG_MISSING → short-circuit, return setup message.
       - Success → real tools injected, loop continues.
  3. LLM calls real tools (research, gmail, send_email, etc.).
     - Blocking tools (email, linkedin, google writes) handle human
       confirmation internally and return the result string.
     - If result starts with "rejected: <feedback>" → LLM sees it and
       rewrites/retries in the same turn.
  4. LLM produces text with no tool calls → done.
  5. Result always routes to validator → output_formatter → END.
     The supervisor never sets next_node.
"""

import logging
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from typing import List
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from agents.base_agent import BaseAgent
from agents_utils.state import AgentState
from agents_utils.context_tracker import snapshot_context_usage
from agents_utils.context_ui_launcher import launch_context_ui
from agents_utils.token_activity_tracker import record_token_event, estimate_tokens
from agents_utils.toolset_registry import (
    load_toolset,
    get_toolset_tools,
    build_setup_required_response,
    set_slack_context,
    CONFIG_MISSING_PREFIX,
)
from slack_utils.handlers_workspace import get_user_preferences_prompt

logger = logging.getLogger(__name__)

MAX_SUPERVISOR_ITERATIONS = 10

# Module-level tool status callback — set by slack_app before graph runs
_tool_status_callback = None


def set_tool_status_callback(fn):
    global _tool_status_callback
    _tool_status_callback = fn


def clear_tool_status_callback():
    global _tool_status_callback
    _tool_status_callback = None


class SupervisorAgent(BaseAgent):

    def __init__(self):
        super().__init__("supervisor")
        self._meta_tool = self._make_load_toolset_tool()

    def _make_load_toolset_tool(self):
        class Input(BaseModel):
            toolset_names: List[str] = Field(
                description="One or more toolsets to load. Available: research, gmail, calendar, docs, sheets, email, email_watch, linkedin"
            )

        def _load(toolset_names: List[str]) -> str:
            return load_toolset(toolset_names)

        return StructuredTool.from_function(
            func=_load, name="load_toolset", args_schema=Input,
            description=(
                "Load tools for the current turn. Pass a list of one or more toolset names. "
                "Returns tool names and descriptions. "
                "Available: research, gmail, calendar, docs, sheets, email, email_watch, linkedin."
            ),
        )

    def get_system_prompt(self) -> str:
        from system_prompts.supervisor_prompt import get_prompt
        base = get_prompt()
        prefs = get_user_preferences_prompt()
        return base + ("\n\n" + prefs if prefs else "")

    def think(self, state: AgentState) -> dict:
        messages      = state.get("messages", [])
        system_prompt = self.get_system_prompt()
        llm_messages  = [SystemMessage(content=system_prompt)] + messages

        current_tools: list       = []
        loaded_toolset_name: str  = ""
        session_id   = getattr(self, "_current_session_id", "")
        project_name = getattr(self, "_current_project_name", "")

        for _ in range(MAX_SUPERVISOR_ITERATIONS):
            if _tool_status_callback:
                try:
                    _tool_status_callback("thinking")
                except Exception:
                    pass

            bound_tools        = [self._meta_tool] + current_tools
            self.llm_with_tools = self.llm.bind_tools(bound_tools)
            response            = self._call_llm(llm_messages, use_tools=True)
            raw_text            = self._extract_response_content(response)
            tool_calls          = getattr(response, "tool_calls", None) or []

            if not tool_calls:
                final_text = raw_text.strip() if raw_text else ""
                return self._finish(state, messages, final_text, loaded_toolset_name)

            llm_messages.append(response)

            for tc in tool_calls:
                name    = tc.get("name", "")
                args    = tc.get("args", {})
                tool_id = tc.get("id", name)

                if name == "load_toolset":
                    if _tool_status_callback:
                        try:
                            _tool_status_callback("load_toolset")
                        except Exception:
                            pass
                    toolset_names = args.get("toolset_names") or args.get("toolset_name") or []
                    if isinstance(toolset_names, str):
                        toolset_names = [toolset_names]
                    
                    if session_id:
                        record_token_event(
                            session_id=session_id, event_type="tool_input",
                            token_count=estimate_tokens(str(args)),
                            model=self.model_name, project_name=project_name,
                        )
                    
                    result = load_toolset(toolset_names)
                    
                    if session_id:
                        record_token_event(
                            session_id=session_id, event_type="tool_output",
                            token_count=estimate_tokens(result),
                            model=self.model_name, project_name=project_name,
                        )

                    if result.startswith(CONFIG_MISSING_PREFIX):
                        feature = result[len(CONFIG_MISSING_PREFIX):].strip()
                        logger.info(f"[supervisor] CONFIG_MISSING: {feature}")
                        # Strip the user's request from messages — no agent response was generated
                        cleaned_messages = list(messages)
                        for i in range(len(cleaned_messages) - 1, -1, -1):
                            if isinstance(cleaned_messages[i], HumanMessage):
                                cleaned_messages.pop(i)
                                break
                        return {
                            "formatted_output": build_setup_required_response(feature),
                            "next_node":        "output_formatter",
                            "task_complete":    True,
                            "active_agent":     "supervisor",
                            "loaded_toolset":   "",
                            "messages":         cleaned_messages,
                        }

                    if result.startswith("ERROR:"):
                        llm_messages.append(ToolMessage(content=result, tool_call_id=tool_id))
                        continue

                    loaded_toolset_name = ", ".join(toolset_names)
                    new_tools           = get_toolset_tools(toolset_names)
                    # Merge without duplicating already-loaded tools
                    existing_names = {t.name for t in current_tools}
                    current_tools += [t for t in new_tools if t.name not in existing_names]
                    logger.info(f"[supervisor] Loaded {toolset_names}: {[t.name for t in new_tools]}")
                    llm_messages.append(ToolMessage(
                        content=f"Toolsets {toolset_names} loaded. Tools: {result}",
                        tool_call_id=tool_id,
                    ))

                else:
                    # Notify Slack of tool being used (like coding agent)
                    if _tool_status_callback:
                        try:
                            _tool_status_callback(name)
                        except Exception:
                            pass

                    tool_fn = next((t for t in current_tools if t.name == name), None)
                    if tool_fn is None:
                        tool_result = f"Error: Tool '{name}' not found. Load the correct toolset first."
                    else:
                        try:
                            tool_result = str(tool_fn.invoke(args))
                        except Exception as e:
                            tool_result = f"Tool error ({name}): {e}"

                    if session_id:
                        record_token_event(
                            session_id=session_id, event_type="tool_input",
                            token_count=estimate_tokens(str(args)),
                            model=self.model_name, project_name=project_name,
                        )
                        record_token_event(
                            session_id=session_id, event_type="tool_output",
                            token_count=estimate_tokens(tool_result),
                            model=self.model_name, project_name=project_name,
                        )

                    logger.info(f"[supervisor] {name} → {tool_result[:120]}")
                    llm_messages.append(ToolMessage(content=tool_result, tool_call_id=tool_id))

        logger.warning("[supervisor] Max iterations reached.")
        return self._finish(state, messages,
                            "I reached my processing limit. Please try a simpler request.",
                            loaded_toolset_name)

    def _finish(self, state: AgentState, original_messages: list,
                final_text: str, loaded_toolset_name: str) -> dict:
        new_messages = original_messages + [AIMessage(content=final_text)]

        # Context snapshot
        try:
            snapshot_context_usage(
                session_id=state.get("slack_thread_ts", ""),
                project_name=f"assistant\\{state.get('project_name', '')}",
                messages=new_messages,
                system_prompt=self.get_system_prompt(),
                model=self.model_name,
                include_tool_schemas=False,
                task_complete=False,
                workspace_path="",
                agent_notes_enabled=False,
            )
        except Exception as e:
            logger.warning(f"[supervisor] Context snapshot failed: {e}")

        return {
            "messages":       new_messages,
            "next_node":      "validator",   # always — no routing decisions here
            "task_complete":  True,
            "active_agent":   "supervisor",
            "loaded_toolset": loaded_toolset_name,
        }


# ── Singleton + node ──────────────────────────────────────────────────────────

_supervisor_agent = None


def get_supervisor_agent() -> SupervisorAgent:
    global _supervisor_agent
    if _supervisor_agent is None:
        _supervisor_agent = SupervisorAgent()
    return _supervisor_agent


def supervisor_agent_node(state: AgentState) -> dict:
    agent = get_supervisor_agent()

    # Inject Slack context for blocking tools (email, linkedin, google write)
    # _slack_client is set by graph.set_slack_client() which is called from slack_app
    # Import lazily to avoid circular import (graph imports supervisor)
    try:
        import agents_utils.graph as _graph_module
        slack_client = getattr(_graph_module, "_slack_client", None)
    except Exception:
        slack_client = None

    thread_ts = state.get("slack_thread_ts", "")
    channel   = state.get("slack_channel", "")
    set_slack_context(slack_client, thread_ts, channel)

    # Launch UI before thinking starts so it's visible during the whole session
    try:
        launch_context_ui()
    except Exception as e:
        logger.warning(f"[supervisor] UI launch failed: {e}")

    logger.info("[supervisor] Thinking...")
    result = agent.think(state)
    result["retry_counts"] = {}
    logger.info(f"[supervisor] Done. toolset={result.get('loaded_toolset', '')}")
    return result
