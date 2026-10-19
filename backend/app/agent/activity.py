"""Request-scoped operational events; never model prompts or private reasoning."""

import inspect
import time
from contextlib import contextmanager
from contextvars import ContextVar

_sink = ContextVar("investigation_activity_sink", default=None)

LABELS = {
    "classify_intent": "Understanding the reported issue",
    "understand_request": "Preparing the investigation",
    "understand_question": "Understanding the knowledge question",
    "analyze_ticket": "Extracting symptoms and missing information",
    "identify_entities": "Identifying affected systems",
    "load_memory": "Checking previous confirmed facts",
    "retrieval_context": "Preparing knowledge sources",
    "plan": "Planning the investigation",
    "formulate_retrieval_plan": "Planning knowledge retrieval",
    "formulate_investigation_plan": "Planning the investigation",
    "execute_tools": "Executing investigation tools",
    "observe": "Evaluating collected evidence",
    "reason": "Comparing evidence and identifying gaps",
    "verify": "Validating recommendations",
    "ask_human": "Requesting necessary information",
    "research": "Researching the remaining evidence gap",
    "remember": "Saving confirmed facts",
    "finalize": "Preparing the final response",
    "general_chat": "Preparing the response",
    "reject_scope": "Checking request scope",
}


@contextmanager
def activity_scope(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


async def emit(stage, status, summary=None, **details):
    sink = _sink.get()
    if sink:
        result = sink({"kind": "workflow", "stage": stage, "status": status,
                       "summary": summary or LABELS.get(stage, "Working on the investigation"),
                       **details})
        if inspect.isawaitable(result):
            await result


def instrument(stage, function):
    async def node(state):
        started = time.monotonic()
        await emit(stage, "running")
        try:
            value = await function(state)
        except BaseException as error:
            from langgraph.errors import GraphInterrupt
            import asyncio

            status = "waiting" if isinstance(error, GraphInterrupt) else (
                "cancelled" if isinstance(error, asyncio.CancelledError) else "failed")
            await emit(stage, status)
            raise
        await emit(stage, "completed", duration_ms=round((time.monotonic() - started) * 1000))
        return value
    return node
