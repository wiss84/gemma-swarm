"""
Gemma Swarm — Main Graph (redesigned)
========================================
Minimal linear graph. The supervisor handles everything tool-side.

Flow:
    input_router → [memory?] → guard_rails → supervisor → validator → output_formatter → END
    interrupt_node can intercept at any point.

No confirm/send nodes. Email, LinkedIn, and Google write actions are blocking
tools inside the supervisor's agentic loop — they handle human confirmation
internally and return results to the supervisor.
"""

import logging
import threading
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END

from agents_utils.state import AgentState, default_state
from agents_utils.config import LANGGRAPH_RECURSION_LIMIT
from agents_utils.memory import get_checkpointer

from agents.supervisor_agent import supervisor_agent_node, get_supervisor_agent
from agents.memory_agent     import memory_agent_node, get_memory_agent

from nodes.input_router    import input_router_node
from nodes.guard_rails     import guard_rails_node
from nodes.validator       import response_validator_node
from nodes.human_gate      import interrupt_node, general_confirm_node
from nodes.output_formatter import output_formatter_node

logger = logging.getLogger(__name__)

# Injected at app start — used by supervisor_agent_node and blocking tools
_slack_client     = None
_slack_channel_fn = None

# Rate limiters registry for context monitor
_rate_limiters: dict = {}


def set_slack_client(client, channel_fn):
    global _slack_client, _slack_channel_fn
    _slack_client     = client
    _slack_channel_fn = channel_fn


# ── Session injection ──────────────────────────────────────────────────────────

def _inject_session(state: AgentState) -> None:
    session_id   = state.get("slack_thread_ts", "")
    project_name = f"assistant\\{state.get('project_name', '')}"
    for agent_fn in [get_supervisor_agent, get_memory_agent]:
        try:
            agent = agent_fn()
            agent._current_session_id   = session_id
            agent._current_project_name = project_name
        except Exception:
            pass


# ── Node wrappers ──────────────────────────────────────────────────────────────

def _input_router(state: AgentState) -> dict:
    return input_router_node(state)


def _guard_rails(state: AgentState) -> dict:
    return guard_rails_node(state)


def _supervisor(state: AgentState) -> dict:
    _inject_session(state)
    return supervisor_agent_node(state)


def _memory(state: AgentState) -> dict:
    _inject_session(state)
    return memory_agent_node(state)


def _validator(state: AgentState) -> dict:
    return response_validator_node(state)


def _output_formatter(state: AgentState) -> dict:
    return output_formatter_node(state)


def _interrupt_gate(state: AgentState) -> dict:
    return interrupt_node(state, client=_slack_client)


def _general_confirm(state: AgentState) -> dict:
    return general_confirm_node(state, client=_slack_client)


# ── Routing ────────────────────────────────────────────────────────────────────

def _should_interrupt(state: AgentState) -> bool:
    return state.get("is_interrupted", False)


def _route_after_input_router(state: AgentState) -> str:
    if _should_interrupt(state):
        return "interrupt_node"
    if state.get("next_node") == "memory":
        return "memory"
    return "guard_rails"


def _route_after_guard_rails(state: AgentState) -> str:
    if _should_interrupt(state):
        return "interrupt_node"
    if state.get("next_node") == "output_formatter":
        return "output_formatter"
    return "supervisor"


def _route_after_supervisor(state: AgentState) -> str:
    if _should_interrupt(state):
        return "interrupt_node"
    # CONFIG_MISSING short-circuit sets next_node="output_formatter" directly
    if state.get("next_node") == "output_formatter":
        return "output_formatter"
    return "validator"


def _route_after_validator(state: AgentState) -> str:
    if _should_interrupt(state):
        return "interrupt_node"
    next_node = state.get("next_node", "output_formatter")
    if next_node == "supervisor":
        return "supervisor"
    if next_node == "human_gate":
        return "general_confirm"
    return "output_formatter"


def _route_after_memory(state: AgentState) -> str:
    if _should_interrupt(state):
        return "interrupt_node"
    return "guard_rails"


def _route_after_interrupt(state: AgentState) -> str:
    decision = state.get("human_decision", "").lower().strip()
    if decision == "rejected":
        return "supervisor"
    return "end"


def _route_after_general_confirm(state: AgentState) -> str:
    decision = state.get("human_decision", "").lower().strip()
    return "supervisor" if decision in ("approved", "rejected") else "output_formatter"


# ── Graph builder ──────────────────────────────────────────────────────────────

def _build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("input_router",    _input_router)
    graph.add_node("guard_rails",     _guard_rails)
    graph.add_node("supervisor",      _supervisor)
    graph.add_node("memory",          _memory)
    graph.add_node("interrupt_node",  _interrupt_gate)
    graph.add_node("general_confirm", _general_confirm)
    graph.add_node("validator",       _validator)
    graph.add_node("output_formatter", _output_formatter)

    graph.set_entry_point("input_router")

    graph.add_conditional_edges("input_router", _route_after_input_router,
        {"memory": "memory", "guard_rails": "guard_rails", "interrupt_node": "interrupt_node"})

    graph.add_conditional_edges("guard_rails", _route_after_guard_rails,
        {"supervisor": "supervisor", "output_formatter": "output_formatter", "interrupt_node": "interrupt_node"})

    graph.add_conditional_edges("supervisor", _route_after_supervisor,
        {"validator": "validator", "output_formatter": "output_formatter", "interrupt_node": "interrupt_node"})

    graph.add_conditional_edges("memory", _route_after_memory,
        {"guard_rails": "guard_rails", "interrupt_node": "interrupt_node"})

    graph.add_conditional_edges("validator", _route_after_validator,
        {"supervisor": "supervisor", "general_confirm": "general_confirm", "output_formatter": "output_formatter"})

    graph.add_conditional_edges("interrupt_node", _route_after_interrupt,
        {"supervisor": "supervisor", "end": END})

    graph.add_conditional_edges("general_confirm", _route_after_general_confirm,
        {"supervisor": "supervisor", "output_formatter": "output_formatter"})

    graph.add_edge("output_formatter", END)

    return graph


# ── Compiled graph singleton ───────────────────────────────────────────────────

_graph        = None
_checkpointer = None


def get_graph():
    global _graph, _checkpointer
    if _graph is None:
        _checkpointer = get_checkpointer()
        _graph        = _build_graph().compile(checkpointer=_checkpointer)
        for agent in [get_supervisor_agent(), get_memory_agent()]:
            _rate_limiters[agent.model_name] = agent.rate_limiter
    return _graph
