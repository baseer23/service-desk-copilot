"""Supervisor and two compiled subgraphs. Every capability invocation uses LangChain→MCP."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import urlsplit

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from backend.app.agent.activity import emit, instrument
from backend.app.agent.models import (
    EvidenceGradeResult,
    EvidenceItem,
    FinalResponse,
    InvestigationDecision,
    InvestigationPlan,
    MemoryFact,
    RequestIntent,
    ResearchSelection,
    TicketAnalysis,
    VerificationResult,
)
from backend.app.agent.scope import SCOPE_MESSAGE
from backend.app.agent.state import InvestigationState
from backend.app.agent.tools import AgentToolHarness, PlanValidationError
from backend.app.mcp.client import MCPToolError
from backend.app.models.provider import LLMError
from backend.app.services.source_policy import permits, quality, rank_sources
from backend.app.services.web import relevant_excerpt
from backend.app.services.entities import extract_entities

LABELS = {
    "search_memory": "Memory searched",
    "search_remembered_facts": "Cross-case memory searched",
    "get_authenticated_profile": "Authenticated profile checked",
    "search_semantic_memory": "Personal memory searched",
    "search_previous_tickets": "Previous incidents searched",
    "get_user_context": "User context checked",
    "get_company_context": "Company context checked",
    "get_site_context": "Site context checked",
    "get_device_context": "Device context checked",
    "get_active_alerts": "Active alerts checked",
    "get_recent_changes": "Recent changes checked",
    "search_knowledge": "Knowledge searched",
    "search_graph": "Relationships searched",
    "search_knowledge_entities": "Knowledge entities searched",
    "get_knowledge_entity_context": "Entity relationships inspected",
    "get_entity_document_references": "Entity documents located",
    "neo4j__read-cypher": "Neo4j knowledge graph queried",
    "search_web": "Public sources searched",
    "search_news": "Current news searched",
    "fetch_web_page": "Public page fetched",
    "get_current_time": "Current time checked",
    "calculate_date_time": "Date and time calculated",
    "calculate": "Calculation completed",
    "convert_unit": "Units converted",
    "geocode_location": "Location resolved",
    "get_weather": "Weather checked",
    "search_places": "Places searched",
    "get_route": "Route estimated",
    "convert_currency": "Exchange rate checked",
    "get_holidays": "Holidays checked",
}

logger = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat()


def concise_answer_line(value: str | None, limit: int = 280) -> str | None:
    """Return one display-safe conclusion line; evidence remains in the timeline."""
    if not value:
        return None
    line = " ".join(value.replace("**", "").split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


class ServiceDeskAgent:
    def __init__(self, settings, mcp, provider, checkpointer):
        self.settings, self.provider = settings, provider
        self.harness = AgentToolHarness(mcp)
        self._case_locks = {}
        self._request_events = {}
        self.knowledge_subgraph = self._subgraph("knowledge")
        self.case_subgraph = self._subgraph("service_desk")
        builder = StateGraph(InvestigationState)
        for name in (
            "classify_intent",
            "understand_request",
            "plan",
            "execute_tools",
            "observe",
            "reason",
            "verify",
            "ask_human",
            "research",
            "remember",
            "finalize",
        ):
            builder.add_node(name, instrument(name, getattr(self, name)))
        builder.add_node("reject_scope", instrument("reject_scope", self.reject_scope))
        builder.add_edge("reject_scope", END)
        builder.add_node("knowledge_subgraph", self.knowledge_subgraph)
        builder.add_node("case_subgraph", self.case_subgraph)
        builder.add_edge(START, "classify_intent")
        builder.add_conditional_edges(
            "classify_intent",
            self.intent_route,
        )
        builder.add_conditional_edges(
            "understand_request",
            lambda s: "knowledge_subgraph" if s["workflow"] == "knowledge" else "case_subgraph",
        )
        for sub in ("knowledge_subgraph", "case_subgraph"):
            builder.add_edge(sub, "reason")
        builder.add_edge("plan", "execute_tools")
        builder.add_edge("execute_tools", "observe")
        builder.add_edge("observe", "reason")
        builder.add_conditional_edges("reason", self.route)
        builder.add_edge("ask_human", "plan")
        builder.add_edge("research", "observe")
        builder.add_conditional_edges("verify", self.verification_route)
        builder.add_edge("remember", "finalize")
        builder.add_edge("finalize", END)
        self.graph = builder.compile(checkpointer=checkpointer)

    def _subgraph(self, workflow):
        graph = StateGraph(InvestigationState)
        if workflow == "knowledge":
            graph.add_node(
                "knowledge_retrieval",
                instrument("knowledge_retrieval", self.retrieve_knowledge),
            )
            graph.add_node("understand_question", instrument("understand_question", self.understand_question))
            graph.add_node("retrieval_context", instrument("retrieval_context", self.retrieval_context))
            graph.add_node("formulate_retrieval_plan", instrument("plan", self.plan))
            graph.add_edge(START, "understand_question")
            graph.add_edge("understand_question", "retrieval_context")
            graph.add_edge("retrieval_context", "formulate_retrieval_plan")
            graph.add_edge("formulate_retrieval_plan", "knowledge_retrieval")
        else:
            graph.add_node("execute_tools", instrument("execute_tools", self.execute_tools))
            for name in (
                "analyze_ticket",
                "identify_entities",
                "load_memory",
                "formulate_investigation_plan",
            ):
                graph.add_node(
                    name,
                    instrument(name, self.plan if name == "formulate_investigation_plan" else getattr(self, name)),
                )
            graph.add_edge(START, "analyze_ticket")
            graph.add_edge("analyze_ticket", "identify_entities")
            graph.add_edge("identify_entities", "load_memory")
            graph.add_edge("load_memory", "formulate_investigation_plan")
            graph.add_edge("formulate_investigation_plan", "execute_tools")
        graph.add_node("observe", instrument("observe", self.observe))
        graph.add_edge(
            "knowledge_retrieval" if workflow == "knowledge" else "execute_tools",
            "observe",
        )
        graph.add_edge("observe", END)
        return graph.compile()

    async def initialize(self):
        await self.harness.initialize()

    @staticmethod
    def config(case_id):
        return {"configurable": {"thread_id": case_id}, "recursion_limit": 180}

    async def note(self, state, tool, text, source="agent", event_key=None):
        if state.get("persist_case") is False:
            # Knowledge-request activity is application logging, not case memory.
            events = self._request_events.setdefault(state["case_id"], [])
            if event_key and any(e.get("event_key") == event_key for e in events):
                return
            events.append(
                {
                    "id": len(events) + 1,
                    "tool": tool,
                    "source": source,
                    "content": text,
                    "created_at": now(),
                    "event_key": event_key,
                }
            )
            return
        await self.harness.invoke(
            "save_case_note",
            {
                "case_id": state["case_id"],
                "tool": tool,
                "text": text,
                "source": source,
                "event_key": event_key,
            },
        )

    async def _call(self, state, name, arguments):
        if name == "fetch_web_page":
            count = state.get("web_pages_fetched", 0)
            if count >= self.settings.max_web_page_fetches:
                raise MCPToolError("Public page fetch limit reached")
            state["web_pages_fetched"] = count + 1
        if (
            name in {"fetch_web_page", "ingest_text"}
            and arguments.get("url")
            and not permits(arguments["url"], self.settings)
        ):
            raise MCPToolError("Public source excluded by configured policy")
        knowledge_mode = str(arguments.get("mode") or "HYBRID").upper()
        knowledge_summary = {
            "HYBRID": "Searching linked graph references and local semantic knowledge",
            "GRAPH": "Searching linked knowledge references",
            "VECTOR": "Searching local semantic knowledge",
            "KEYWORD": "Searching local indexed text",
        }.get(knowledge_mode, "Searching internal knowledge")
        summaries = {
            "search_knowledge": knowledge_summary,
            "search_graph": "Querying knowledge relationships",
            "search_knowledge_entities": "Searching linked knowledge entities",
            "get_knowledge_entity_context": "Inspecting linked entity context",
            "get_entity_document_references": "Locating documents linked to the entity",
            "search_news": "Searching current news",
            "search_web": "Searching the public web",
            "fetch_web_page": "Reading a public source",
            "calculate_date_time": "Calculating date and time",
            "calculate": "Calculating exact arithmetic",
            "convert_unit": "Converting units",
            "geocode_location": "Resolving the location",
            "get_weather": "Checking observed and forecast weather",
            "search_places": "Searching mapped places",
            "get_route": "Estimating route distance and duration",
            "convert_currency": "Checking a dated exchange rate",
            "get_holidays": "Checking scoped public holidays",
        }
        summary = summaries.get(name, "Reading connected tool evidence")
        details = {"tool": name}
        if name in {"search_web", "search_news", "search_knowledge", "search_places"}:
            details["query"] = str(arguments.get("query") or arguments.get("question") or "")[:500]
        elif name in {"get_weather", "geocode_location"}:
            details["query"] = str(arguments.get("location") or arguments.get("query") or "")[:500]
        elif name == "fetch_web_page":
            details["source_host"] = urlsplit(str(arguments.get("url", ""))).hostname or "public source"
        await emit("tool:" + name, "running", summary, **details)
        try:
            result = await self.harness.invoke(name, arguments)
        except BaseException:
            await emit("tool:" + name, "failed", summary, tool=name)
            raise
        result = self._normalize_capability_result(name, result)
        result_items = result.get("items", []) if isinstance(result, dict) else result
        result_count = len(result_items) if isinstance(result_items, list) else 0
        findings = []
        sources = []
        if isinstance(result_items, list):
            for item in result_items[:3]:
                if not isinstance(item, dict):
                    continue
                metadata = item.get("metadata", {})
                label = (
                    item.get("title") or metadata.get("title")
                    or item.get("name") or item.get("source")
                )
                if not label and item.get("url"):
                    label = item["url"]
                if label:
                    findings.append(str(label)[:240])
                    sources.append(
                        {
                            "title": str(label)[:200],
                            "url": item.get("url") or metadata.get("url") or metadata.get("source"),
                            "page": metadata.get("page"),
                            "extraction_method": metadata.get("extraction_method"),
                        }
                    )
        completed_summary = (
            f"Retrieved {result_count} document candidate"
            + ("s" if result_count != 1 else "")
            if name == "search_knowledge"
            else summary
        )
        completed_status = (
            "failed"
            if isinstance(result, dict) and result.get("status") in {"error", "unavailable"}
            else "completed"
        )
        await emit(
            "tool:" + name,
            completed_status,
            completed_summary,
            **details,
            result_count=result_count,
            findings=findings,
            sources=sources,
            result_role="retrieval_candidates" if name == "search_knowledge" else "observations",
        )
        if name == "fetch_web_page" and isinstance(result, dict):
            page = result.get("result") or result
            if not permits(page.get("url", arguments.get("url", "")), self.settings):
                raise MCPToolError("Redirected public source excluded by configured policy")
        rows = (
            result.get("items", [])
            if isinstance(result, dict)
            else result
            if isinstance(result, list)
            else []
        )
        await self.note(
            state,
            name,
            f"{LABELS.get(name,name)}: {len(rows)} result(s)",
            result.get("source_system", "capability") if isinstance(result, dict) else "capability",
        )
        return result

    @staticmethod
    def _normalize_capability_result(name, value):
        """Normalize built-in and discovered MCP response shapes at one boundary."""
        if isinstance(value, dict) and "items" in value:
            result = dict(value)
            result.setdefault("tool", name)
            result.setdefault("status", "ok")
            result.setdefault("timestamp", now())
            result.setdefault("reliability", 0.6)
            return result
        if isinstance(value, dict) and "result" in value:
            result = dict(value)
            payload = result.get("result")
            result.setdefault("tool", name)
            result.setdefault("source_system", "capability")
            result.setdefault("status", "ok")
            result.setdefault("timestamp", now())
            result.setdefault("reliability", 0.6)
            result["items"] = (
                payload if isinstance(payload, list)
                else [payload] if isinstance(payload, dict)
                else []
            )
            return result
        if isinstance(value, list):
            if all(isinstance(item, dict) for item in value):
                return {
                    "tool": name,
                    "source_system": "capability",
                    "timestamp": now(),
                    "reliability": 0.6,
                    "status": "ok",
                    "items": value,
                    "result": value,
                }
        elif isinstance(value, dict):
            if value.get("available") is False:
                return {
                    **value,
                    "tool": value.get("tool") or name,
                    "source_system": value.get("source_system", "tool_error"),
                    "timestamp": value.get("timestamp") or now(),
                    "reliability": 0,
                    "status": "unavailable",
                    "error": value.get("reason") or "Graph integration is unavailable",
                    "items": [],
                }
            if value.get("status") in {"error", "unavailable"}:
                return {
                    **value,
                    "tool": value.get("tool") or name,
                    "timestamp": value.get("timestamp") or now(),
                    "reliability": value.get("reliability", 0),
                    "items": [],
                }
            return {
                "tool": name,
                "source_system": value.get("source_system", "capability"),
                "timestamp": now(),
                "reliability": 0.6,
                "status": "ok",
                "items": [value],
                "result": value,
            }
        return {
            "tool": name,
            "source_system": "tool_error",
            "timestamp": now(),
            "reliability": 0,
            "status": "error",
            "error": "Capability returned an unsupported response shape",
            "items": [],
            "result": None,
        }

    async def run_case(self, case_id, answer=None):
        lock = self._case_locks.setdefault(case_id, asyncio.Lock())
        async with lock:
            stored = (await self.harness.invoke("get_case", {"case_id": case_id}))["result"]
            if not stored:
                raise KeyError(case_id)
            checkpoint = await self.graph.aget_state(self.config(case_id))
            if answer is not None:
                if not checkpoint.next or stored.get("status") != "needs_input":
                    raise ValueError("This case is not awaiting human input")
                graph_input = Command(resume=answer)
            elif checkpoint.values:
                if stored.get("status") == "error" and checkpoint.next:
                    graph_input = None
                else:
                    return await self._save(case_id, dict(checkpoint.values))
            else:
                graph_input = {
                    **stored,
                    "status": "investigating",
                    "evidence": [
                        EvidenceItem(
                            id=sha256((case_id + stored["ticket"]["summary"]).encode()).hexdigest()[
                                :20
                            ],
                            source_system="human_input",
                            source_type="human_report",
                            tool="user_request",
                            timestamp=now(),
                            observed_at=now(),
                            source="ticket_report",
                            content={"reported_request": stored["ticket"]["summary"]},
                            reliability=0.7,
                            case_id=case_id,
                        ).model_dump()
                    ],
                    "confirmed_facts": [],
                    "remembered_facts": [],
                    "human_answers": [],
                    "contradictions": [],
                    "actions_already_taken": [],
                    "research_iteration": 0,
                    "research_rounds": 0,
                }
            try:
                await self.graph.ainvoke(graph_input, self.config(case_id))
                snapshot = await self.graph.aget_state(self.config(case_id))
                return await self._save(case_id, dict(snapshot.values))
            except Exception:
                snapshot = await self.graph.aget_state(self.config(case_id))
                state = dict(snapshot.values or stored)
                state.update(
                    status="error",
                    uncertainty=["Investigation failed; the API error identifies the failure."],
                )
                await self._save(case_id, state)
                await self.note(state, "error", "Investigation failed", "application")
                raise

    async def _save(self, case_id, state):
        await self.harness.invoke("save_case", {"case_id": case_id, "state": state})
        return state

    async def run_request(self, request_id, summary=None, answer=None, message=None):
        lock = self._case_locks.setdefault(request_id, asyncio.Lock())
        async with lock:
            return await self._run_request(request_id, summary, answer, message)

    async def _run_request(self, request_id, summary=None, answer=None, message=None):
        """Classify before creating operational state; knowledge never creates a case."""
        from backend.app.core.principal import current_principal

        owner = current_principal(self.settings).owner_id
        if message is not None:
            snapshot = await self.graph.aget_state(self.config(request_id))
            if not snapshot.values:
                raise KeyError(request_id)
            if snapshot.values.get("request_owner") != owner:
                raise KeyError(request_id)
            if snapshot.next or snapshot.values.get("persist_case"):
                raise ValueError("Continue this investigation using its pending question")
            previous = dict(snapshot.values)
            references = list(
                previous.get("conversation_context", {}).get("document_references", [])
            )
            known_reference_ids = {
                (item.get("document_id"), item.get("title"), item.get("page"))
                for item in references
            }
            for item in previous.get("evidence", []):
                if item.get("source_type") != "document":
                    continue
                content = item.get("content", {})
                metadata = content.get("metadata", {})
                reference = {
                    "document_id": item.get("doc_id") or metadata.get("doc_id"),
                    "title": content.get("title") or metadata.get("title"),
                    "page": metadata.get("page"),
                    "source": item.get("source"),
                }
                key = (reference["document_id"], reference["title"], reference["page"])
                if key not in known_reference_ids:
                    references.append(reference)
                    known_reference_ids.add(key)
            graph_input = {
                "ticket": {"summary": message},
                "status": "investigating",
                "intent": {},
                "evidence": [],
                "retrieval_candidates": [],
                "decision": {},
                "verification": {},
                "research_rounds": 0,
                "research_iteration": 0,
                "web_pages_fetched": 0,
                "executed_tool_calls": [],
                "final_answer": None,
                "final_response": {},
                "pending_question": None,
                "chat_messages": previous.get("chat_messages", [])[-16:],
                "conversation_context": {
                    "previous_request": previous.get("ticket", {}).get("summary"),
                    "resolved_goal": previous.get("intent", {}).get("goal"),
                    "document_references": references[-12:],
                },
            }
            self._request_events[request_id] = []
        elif answer is None:
            graph_input = {
                "case_id": request_id,
                "ticket": {"summary": summary},
                "persist_case": False,
                "route_request": True,
                "status": "investigating",
                "evidence": [
                    EvidenceItem(
                        id=sha256((request_id + summary).encode()).hexdigest()[:20],
                        source_system="human_input",
                        source_type="human_report",
                        tool="user_request",
                        timestamp=now(),
                        observed_at=now(),
                        source="request_report",
                        content={"reported_request": summary},
                        reliability=0.7,
                    ).model_dump()
                ],
                "retrieval_candidates": [],
                "confirmed_facts": [],
                "remembered_facts": [],
                "personal_memory": [],
                "human_answers": [],
                "contradictions": [],
                "research_iteration": 0,
                "research_rounds": 0,
                "web_pages_fetched": 0,
                "executed_tool_calls": [],
                "chat_messages": [],
                "conversation_context": {},
                "request_owner": owner,
            }
            self._request_events[request_id] = []
        else:
            snapshot = await self.graph.aget_state(self.config(request_id))
            if (
                not snapshot.values.get("persist_case")
                and snapshot.values.get("request_owner") != owner
            ):
                raise KeyError(request_id)
            if not snapshot.next or snapshot.values.get("status") != "needs_input":
                raise ValueError("This request is not awaiting human input")
            graph_input = Command(resume=answer)
        memory_query = message if message is not None else summary if summary is not None else answer
        personal_memory = []
        if memory_query:
            try:
                memory_result = await self.harness.invoke(
                    "search_semantic_memory", {"query": memory_query, "limit": 5}
                )
                personal_memory = (memory_result.get("result") or {}).get("facts", [])
            except Exception:
                personal_memory = []
        if isinstance(graph_input, dict):
            graph_input["personal_memory"] = personal_memory
        elif personal_memory:
            await self.graph.aupdate_state(
                self.config(request_id), {"personal_memory": personal_memory}
            )
        try:
            await self.graph.ainvoke(graph_input, self.config(request_id))
            state = dict((await self.graph.aget_state(self.config(request_id))).values)
            if state.get("persist_case"):
                await self._save(request_id, state)
                events = (await self.harness.invoke("get_case_history", {"case_id": request_id}))[
                    "items"
                ]
            else:
                events = list(self._request_events.get(request_id, []))
            return state, events
        except Exception:
            state = dict((await self.graph.aget_state(self.config(request_id))).values)
            if state.get("persist_case"):
                state.update(status="error", uncertainty=["Request failed; retry this saved case."])
                await self._save(request_id, state)
                await self.note(state, "error", "Investigation failed", "application")
            raise
        finally:
            snapshot = await self.graph.aget_state(self.config(request_id))
            if snapshot.values.get("status") != "needs_input":
                self._request_events.pop(request_id, None)

    def context(self, state, include_tools=False):
        # Preserve full evidence in storage/UI; bound the model context, retaining
        # all human facts, contradictions and the newest source records.
        evidence = []
        for row in state.get("evidence", [])[-24:]:
            content = row["content"]
            meta = content.get("metadata", {})
            body = {
                k: v
                for k, v in content.items()
                if k
                not in {
                    "source_system",
                    "source",
                    "observed_at",
                    "metadata",
                    "content_hash",
                    "id",
                    "case_id",
                }
            }
            if "text" in body:
                body["text"] = relevant_excerpt(str(body["text"]), state["ticket"]["summary"], 1600)
                body["title"] = meta.get("title") or body.get("title")
                body["url"] = row.get("url")
            evidence.append(
                {
                    k: row[k]
                    for k in (
                        "id",
                        "tool",
                        "source_system",
                        "source_type",
                        "timestamp",
                        "reliability",
                    )
                }
                | {"content": body}
            )
        result = {
            k: state.get(k)
            for k in (
                "ticket",
                "workflow",
                "intent",
                "explicit_saved_knowledge",
                "analysis",
                "remembered_facts",
                "personal_memory",
                "confirmed_facts",
                "human_answers",
                "contradictions",
                "decision",
                "verification",
                "decision_issues",
                "research_rounds",
                "available_context",
                "executed_tool_calls",
                "conversation_context",
            )
        }
        result["conversation"] = state.get("chat_messages", [])[-8:]
        result["evidence"] = evidence
        result["retrieval_assessment"] = [
            {
                "document_id": item.get("doc_id")
                or item.get("content", {}).get("metadata", {}).get("doc_id"),
                "title": item.get("content", {}).get("title")
                or item.get("content", {}).get("metadata", {}).get("title"),
                "page": item.get("content", {}).get("metadata", {}).get("page"),
                "assessment": item.get("assessment"),
                "reason": item.get("assessment_reason"),
            }
            for item in state.get("retrieval_candidates", [])[-12:]
        ]
        if include_tools:
            result["available_tools"] = self.harness.schemas(state.get("workflow", "service_desk"))
        return result

    async def classify_intent(self, state):
        intent = await self.provider.generate_structured(
            "classify_intent",
            {
                "request": state["ticket"]["summary"],
                "knowledge_scope": self.settings.knowledge_scope,
                "conversation": state.get("chat_messages", [])[-8:],
                "conversation_context": state.get("conversation_context", {}),
            },
            RequestIntent,
        )
        # Enforce explicit user-specified source scope, not keyword-based
        # topic routing. The planner still chooses relevant local tools.
        request_text = state["ticket"]["summary"]
        explicit_local = bool(re.search(
            r"(?:\b(?:from|check|search|use)\s+(?:(?:my|our|the|saved|uploaded|internal)\s+){0,3}(?:knowledge|kb)\b"
            r"|\baccording\s+to\s+(?:(?:my|our|the)\s+)?(?:uploaded|saved|local)\s+(?:pdf|document|file|newspaper)\b"
            r"|\b(?:in|from)\s+my\s+(?=[A-Za-z0-9_.-]*\d)[A-Za-z0-9_.-]+\b)",
            request_text, flags=re.IGNORECASE,
        ))
        # Structured classification is authoritative for natural requests such
        # as a document title or a follow-up about an uploaded PDF. Regexes only
        # enforce source scope when the model misses an explicit phrase.
        explicit_local = explicit_local or (
            intent.workflow == "knowledge" and intent.knowledge_source == "documents"
        )
        explicit_public = bool(re.search(
            r"\b(?:search|check|look\s+up)\s+(?:online|the\s+(?:web|internet))\b"
            r"|\blatest\s+official\s+(?:requirements|rules|regulations|guidance)\b",
            request_text, flags=re.IGNORECASE,
        ))
        if explicit_local and (
            intent.workflow != "knowledge" or intent.knowledge_source != "documents"
        ):
            intent = intent.model_copy(update={
                "workflow": "knowledge", "knowledge_source": "documents", "public_topic": "none",
                "justification": (
                    "The user explicitly requested local knowledge. "
                    "Select authorized local evidence before answering."
                ),
            })
            await emit(
                "source_scope", "completed",
                "Source scope constrained to the user's saved knowledge",
                rationale="The request explicitly specified the local knowledge workspace.",
            )
        elif explicit_public and intent.workflow == "knowledge" and intent.knowledge_source != "public_web":
            intent = intent.model_copy(update={
                "knowledge_source": "public_web",
                "public_topic": "web",
                "justification": (
                    "The user explicitly requested current public sources. "
                    "Select authorized web evidence before answering."
                ),
            })
            await emit(
                "source_scope", "completed",
                "Source scope constrained to current public sources",
                rationale="The request explicitly asked for online or latest official information.",
            )
        await self.note(
            state, "classify_intent", f"Request classified: {intent.workflow}", self.provider.name()
        )
        await emit(
            "understanding_summary",
            "completed",
            f"Understood objective: {intent.goal[:240]}",
            rationale=intent.justification[:500],
            workflow=intent.workflow,
        )
        return {
            "intent": intent.model_dump(), "workflow": intent.workflow,
            "explicit_saved_knowledge": explicit_local,
            "chat_messages": state.get("chat_messages", []),
        }

    def intent_route(self, state):
        intent = state["intent"]
        if not intent.get("in_scope", True):
            return "reject_scope"
        return "understand_request"

    async def reject_scope(self, state):
        return {
            "workflow": "knowledge",
            "status": "insufficient_information",
            "final_answer": SCOPE_MESSAGE,
            "final_response": FinalResponse(
                issue_summary=state["ticket"]["summary"], answer=SCOPE_MESSAGE, confidence=0
            ).model_dump(),
            "evidence": [],
            "confidence": 0,
            "uncertainty": [],
            "pending_question": None,
        }

    async def understand_request(self, state):
        update = {"status": "investigating"}
        if state.get("route_request") and state["workflow"] == "service_desk":
            evidence = [{**row, "case_id": state["case_id"]} for row in state.get("evidence", [])]
            state = {**state, "persist_case": True, "evidence": evidence}
            await self._save(state["case_id"], state)
            for event in self._request_events.pop(state["case_id"], []):
                await self.note(state, event["tool"], event["content"], event["source"])
            await self.note(
                state,
                "create_case",
                "Case created after service-desk classification",
                "application",
            )
            update["persist_case"] = True
            update["evidence"] = evidence
        await self.note(state, "understand_request", state["intent"]["goal"], self.provider.name())
        return update

    async def understand_question(self, state):
        await self.note(
            state,
            "understand_question",
            "Knowledge objective interpreted; source needs remain a planning decision",
        )
        return {"analysis": {"symptoms": state["ticket"]["summary"], "entities": []}}

    async def retrieval_context(self, state):
        result = await self.harness.invoke(
            "list_available_context", {"question": state["ticket"]["summary"]}
        )
        return {"available_context": result["result"]}

    async def analyze_ticket(self, state):
        analysis = await self.provider.generate_structured(
            "analyze_ticket", {"ticket": state["ticket"]}, TicketAnalysis
        )
        await self.note(state, "analyze_ticket", "Ticket analyzed", self.provider.name())
        return {"analysis": analysis.model_dump()}

    async def identify_entities(self, state):
        await self.note(
            state,
            "identify_entities",
            "Request identifiers extracted; unknown operational details remain unknown",
        )
        result = await self._call(state, "list_available_context", {})
        return {"available_context": result["result"]}

    async def load_memory(self, state):
        subject = state["analysis"].get("user_name")
        if not subject:
            return {"remembered_facts": []}
        result = await self._call(state, "search_memory", {"subject": subject})
        facts = result["items"]
        evidence = self._evidence(state, "search_memory", result)
        return {"remembered_facts": facts, "evidence": state.get("evidence", []) + evidence}

    async def plan(self, state):
        if state.get("plan") and not state.get("plan_executed"):
            return {}
        if not state.get("tools_discovered"):
            await emit("discover_tools", "running", "Discovering available MCP tools")
            await self.harness.initialize()
            await emit("discover_tools", "completed", "MCP tool schemas validated",
                       tool_count=len(self.harness.tools))
        context = self.context(state, True)
        for attempt in range(2):
            plan = await self.provider.generate_structured("plan", context, InvestigationPlan)
            # A plan that contains retrieval actions necessarily considers
            # additional evidence useful. Normalize this redundant model flag;
            # tool schemas and source-scope validation remain authoritative.
            if plan.tool_calls and not plan.additional_evidence_useful:
                plan.additional_evidence_useful = True
            if state.get("explicit_saved_knowledge"):
                for call in plan.tool_calls:
                    if call.name == "search_knowledge":
                        call.arguments = {**call.arguments, "mode": "HYBRID"}
            try:
                self.harness.validate_plan(plan, state["workflow"])
                if not plan.tool_calls and plan.additional_evidence_useful and not plan.clarification_needed:
                    raise PlanValidationError(
                        "EVIDENCE_FLAG_WITHOUT_ACTION",
                        "The plan says more evidence is required but requests no tool. "
                        "Choose an authorized retrieval tool, ask one justified clarification, "
                        "or, for stable general knowledge only, explicitly set "
                        "additional_evidence_useful=false."
                    )
                if (
                    state.get("workflow") == "knowledge"
                    and state.get("intent", {}).get("knowledge_source") in {"documents", "public_web"}
                    and not plan.tool_calls
                    and not plan.clarification_needed
                ):
                    raise PlanValidationError(
                        "REQUIRED_SOURCE_NOT_SELECTED",
                        "This question needs the selected source scope. Plan a real "
                        "document/web retrieval or request one material clarification."
                    )
                if (
                    state.get("workflow") == "knowledge"
                    and state.get("explicit_saved_knowledge")
                    and not plan.clarification_needed
                    and not any(call.name == "search_knowledge" for call in plan.tool_calls)
                ):
                    raise PlanValidationError(
                        "LOCAL_KNOWLEDGE_RETRIEVAL_REQUIRED",
                        "An explicit saved-knowledge request requires local Chroma retrieval. "
                        "Use search_knowledge; graph participation is enforced separately.",
                    )
                if state.get("explicit_saved_knowledge") and any(
                    call.name in {"search_web", "search_news", "fetch_web_page"}
                    for call in plan.tool_calls
                ):
                    raise PlanValidationError(
                        "SAVED_KNOWLEDGE_PUBLIC_SOURCE_FORBIDDEN",
                        "This request is explicitly scoped to saved documents. Do not use public web tools.",
                    )
                if plan.tool_calls and not plan.additional_evidence_useful:
                    raise PlanValidationError(
                        "TOOL_CALLS_WITH_EVIDENCE_DISABLED",
                        "Tool calls contradict additional_evidence_useful=false. "
                        "Set it true when collecting observations."
                    )
                answered_questions = {
                    str(item.get("question") or "").strip().casefold()
                    for item in state.get("human_answers", [])
                    if item.get("question")
                }
                if (
                    plan.clarification_needed
                    and plan.clarification_question
                    and plan.clarification_question.strip().casefold() in answered_questions
                ):
                    raise PlanValidationError(
                        "REPEATED_CLARIFICATION",
                        "The proposed clarification was already answered. Build a new plan using "
                        "the confirmed human answer."
                    )
                break
            except (ValueError, PlanValidationError) as exc:
                diagnostics = self._plan_validation_diagnostics(
                    state, exc, attempt + 1
                )
                await self._emit_validation_failure(diagnostics)
                if attempt:
                    if state.get("explicit_saved_knowledge"):
                        plan = InvestigationPlan(
                            goal="Answer from the user's saved document evidence",
                            additional_evidence_useful=True,
                            source_strategy="Use tenant-scoped local hybrid retrieval only.",
                            unknowns=["The answer-bearing passage in the named saved document"],
                            tool_calls=[{
                                "name": "search_knowledge",
                                "arguments": {
                                    "question": state["ticket"]["summary"],
                                    "top_k": 5,
                                    "mode": "HYBRID",
                                },
                                "reason": "The request explicitly names the user's saved document.",
                            }],
                            stop_condition="Stop after grounded saved-document evidence is retrieved.",
                        )
                        self.harness.validate_plan(plan, state["workflow"])
                        break
                    if self._asks_authenticated_identity(state):
                        plan = InvestigationPlan(
                            goal="Check the authenticated user's confirmed profile",
                            additional_evidence_useful=True,
                            source_strategy=(
                                "Use the tenant- and owner-scoped authenticated profile capability."
                            ),
                            unknowns=["Whether a confirmed profile name is available"],
                            tool_calls=[{
                                "name": "get_authenticated_profile",
                                "arguments": {},
                                "reason": "The answer must come from confirmed personal memory.",
                            }],
                            stop_condition=(
                                "Stop after the authenticated profile returns a confirmed name or no name."
                            ),
                        )
                        self.harness.validate_plan(plan, state["workflow"])
                        break
                    raise LLMError(
                        "Model plan failed tool permission or argument validation",
                        category="application_validation",
                        request_id=state.get("case_id"),
                        diagnostics=diagnostics,
                    ) from exc
                context["plan_validation_feedback"] = (
                    exc.feedback()
                    if isinstance(exc, PlanValidationError)
                    else
                    exc.errors(include_input=False, include_url=False)
                    if isinstance(exc, ValidationError)
                    else str(exc)
                    if str(exc).startswith("The proposed clarification was already answered")
                    else str(exc)[:500] or "Choose only permitted capabilities from available_tools."
                )
        await self.note(
            state,
            "plan",
            f"Plan: {plan.goal}. " + "; ".join(c.name + ": " + c.reason for c in plan.tool_calls),
            self.provider.name(),
        )
        await emit(
            "research_plan",
            "completed",
            f"Research plan: {plan.goal[:240]}",
            objective=plan.goal[:500],
            rationale=plan.stop_condition[:500],
            source_strategy=plan.source_strategy[:500],
            sufficient_evidence=plan.stop_condition[:500],
            research_questions=plan.unknowns[:6],
            actions=[
                {"tool": call.name, "reason": call.reason[:300]}
                for call in plan.tool_calls
            ],
        )
        selected = [
            f"{LABELS.get(call.name, call.name.replace('__', ' ').replace('_', ' '))} because {call.reason.rstrip('.')}"
            for call in plan.tool_calls
        ]
        skipped = [
            f"{item.source}: {item.reason}" for item in plan.skipped_sources
        ]
        explanation = (
            ("Checking " + "; ".join(selected) + ". " if selected else "No tool lookup is needed. ")
            + ("Skipping " + "; ".join(skipped) + ". " if skipped else "")
            + "Evidence needed: " + ("; ".join(plan.unknowns) or plan.stop_condition)
        )
        await emit(
            "source_decision",
            "completed",
            explanation[:1000],
            objective=plan.goal[:500],
            actions=[
                {"tool": call.name, "reason": call.reason[:300]} for call in plan.tool_calls
            ],
            skipped_sources=[
                {"source": item.source, "reason": item.reason[:300]}
                for item in plan.skipped_sources
            ],
            research_questions=plan.unknowns[:6],
            source_strategy=plan.source_strategy[:500],
            additional_evidence_useful=plan.additional_evidence_useful,
        )
        return {
            "plan": plan.model_dump(),
            "research_questions": plan.unknowns,
            "plan_executed": False,
            "tools_discovered": True,
        }

    @staticmethod
    def _asks_authenticated_identity(state):
        text = " ".join(
            str(item.get("content", ""))
            for item in [*state.get("chat_messages", [])[-4:], {
                "content": state.get("ticket", {}).get("summary", "")
            }]
        ).casefold()
        return bool(
            re.search(r"\b(?:what(?:'s|s|\s+is)|tell\s+me)\s+my\s+(?:name|profile|identity)\b", text)
            or re.search(r"\bwho\s+am\s+i\b", text)
        )

    def _plan_validation_diagnostics(self, state, error, attempt):
        details = error.feedback() if isinstance(error, PlanValidationError) else {
            "validation_error_code": "PLAN_CONTRACT_INVALID",
            "expected_argument_names": [],
            "actual_argument_names": [],
        }
        details.pop("message", None)
        return {
            "request_id": state.get("case_id"),
            "provider": self.provider.name(),
            "stage": "plan",
            "attempt_number": attempt,
            **details,
        }

    async def _emit_validation_failure(self, diagnostics):
        logger.warning(
            "Investigation validation rejected provider=%s stage=%s code=%s "
            "tool=%s expected_arguments=%s actual_arguments=%s attempt=%s request_id=%s",
            diagnostics.get("provider"), diagnostics.get("stage"),
            diagnostics.get("validation_error_code"), diagnostics.get("tool_name"),
            diagnostics.get("expected_argument_names", []),
            diagnostics.get("actual_argument_names", []),
            diagnostics.get("attempt_number"), diagnostics.get("request_id"),
        )
        event_details = {key: value for key, value in diagnostics.items() if key != "stage"}
        await emit(
            diagnostics.get("stage", "application_validation"), "failed",
            "The proposed investigation step did not match its validated contract",
            **event_details,
        )

    async def execute_tools(self, state):
        plan = InvestigationPlan.model_validate(state["plan"])
        try:
            self.harness.validate_plan(plan, state["workflow"])
        except ValueError as exc:
            diagnostics = self._plan_validation_diagnostics(state, exc, 1)
            await self._emit_validation_failure(diagnostics)
            raise LLMError(
                "Model plan failed tool permission or argument validation",
                category="application_validation",
                request_id=state.get("case_id"),
                diagnostics=diagnostics,
            ) from exc
        results = [None] * len(plan.tool_calls)
        attempted_signatures = []
        web_round = any(c.name in {"search_web", "search_news"} for c in plan.tool_calls)

        async def execute(index):
            call = plan.tool_calls[index]
            signature = self._tool_signature(call.name, call.arguments)
            attempted_signatures.append(signature)
            try:
                if (
                    call.name in {"search_web", "search_news"}
                    and state.get("research_rounds", 0) >= self.settings.max_research_rounds
                ):
                    raise MCPToolError("Public research round limit reached")
                output = await self._call(state, call.name, call.arguments)
                results[index] = {
                    "name": call.name,
                    "output": output,
                }
            except MCPToolError as exc:
                await self.note(state, call.name, f"Capability failed: {call.name}", "tool_error")
                results[index] = {
                    "name": call.name,
                    "output": {
                        "tool": call.name,
                        "source_system": "tool_error",
                        "timestamp": now(),
                        "reliability": 0,
                        "status": "error",
                        "error": str(exc),
                        "items": [],
                    },
                }

        grouped = {i for group in plan.parallel_groups for i in group}
        for group in plan.parallel_groups:
            await asyncio.gather(*(execute(i) for i in group))
        for i in range(len(results)):
            if i not in grouped:
                await execute(i)
        # Search results are leads, not verified evidence. The model selects
        # URLs to read before we ask the reasoner to assess the answer.
        for index, call in enumerate(plan.tool_calls):
            if call.name not in {"search_news", "search_web"}:
                continue
            output = results[index]["output"]
            if not isinstance(output, dict) or output.get("status") != "ok":
                continue
            candidates = output.get("items", [])
            if not isinstance(candidates, list):
                continue
            candidates = rank_sources(candidates, self.settings)
            if not candidates:
                await emit("source_selection", "completed", "No permitted web candidates")
                continue
            try:
                selected = await self.provider.generate_structured(
                    "evaluate_sources",
                    {
                        "ticket": state["ticket"],
                        "request": state["ticket"]["summary"],
                        "query": call.arguments.get("query", ""),
                        "results": candidates[:10],
                        "preferred_domains": self.settings.research_preferred_domains,
                        "source_policy": self.settings.research_source_policy,
                        "missing_evidence": [],
                        "selection_phase": "candidate_discovery",
                    },
                    ResearchSelection,
                )
            except LLMError:
                await emit("source_selection", "failed", "Article selection unavailable")
                continue
            urls = {row["url"]: row for row in candidates if row.get("url")}
            selected_urls = list(dict.fromkeys(
                url for url in selected.selected_urls if url in urls
            ))
            await emit(
                "source_selection", "completed",
                f"Selected {len(selected_urls)} news/web article(s) for verification",
                result_count=len(selected_urls),
                rationale=selected.justification[:500],
                sources=[{"title": urls[url].get("title") or url, "url": url}
                         for url in selected_urls],
            )
            # Explicit URLs chosen by the LLM are leads to READ, even if a
            # conflicting Boolean said they were not yet factually verified.
            for url in selected_urls:
                signature = self._tool_signature("fetch_web_page", {"url": url})
                if signature in set(state.get("executed_tool_calls", [])) | set(attempted_signatures):
                    continue
                attempted_signatures.append(signature)
                try:
                    fetched = await self._call(state, "fetch_web_page", {"url": url})
                except MCPToolError as exc:
                    await emit("source_fetch", "failed", "Public article unavailable",
                               source_host=urlsplit(url).hostname)
                    results.append({
                        "name": "fetch_web_page",
                        "output": {"status": "error", "error": str(exc), "items": [],
                                   "source_system": "tool_error", "timestamp": now()},
                    })
                    continue
                if not isinstance(fetched, dict) or fetched.get("status") != "ok":
                    await emit(
                        "source_fetch", "failed", "Selected article could not be read",
                        source_host=urlsplit(url).hostname,
                        rationale=str(fetched.get("error") or "MCP webpage reader unavailable")[:300]
                        if isinstance(fetched, dict) else "Unexpected webpage response",
                    )
                    results.append({"name": "fetch_web_page", "output": fetched})
                    continue
                page = fetched.get("result")
                if not isinstance(page, dict) or len(str(page.get("text") or "").strip()) < 120:
                    await emit("source_fetch", "failed",
                               "Selected article did not provide enough readable text",
                               source_host=urlsplit(url).hostname)
                    continue
                page = {
                    **page, "published_at": urls[url].get("published_at"),
                    "publisher": urls[url].get("publisher"),
                    "source_quality": quality(page.get("url") or url, self.settings),
                }
                results.append({"name": "fetch_web_page", "output": {
                    **fetched, "result": page, "items": [page]}})
                await emit("source_fetched", "completed", "Read article for evidence review",
                           sources=[{"title": page.get("title") or url, "url": page.get("url") or url}])
        return {
            "tool_results": results,
            "web_pages_fetched": state.get("web_pages_fetched", 0),
            "plan_executed": True,
            "executed_tool_calls": list(
                dict.fromkeys(state.get("executed_tool_calls", []) + attempted_signatures)
            ),
            "research_rounds": state.get("research_rounds", 0) + int(web_round),
        }

    async def retrieve_knowledge(self, state):
        """Run required graph participation alongside the validated retrieval plan."""
        if not state.get("explicit_saved_knowledge"):
            return await self.execute_tools(state)
        planned, graph_result = await asyncio.gather(
            self.execute_tools(state), self._mandatory_saved_knowledge_graph_read(state)
        )
        graph_outputs = [graph_result]
        if graph_result.get("hydrated_output"):
            graph_outputs.append({
                "name": "search_knowledge",
                "output": graph_result["hydrated_output"],
            })
        return {
            **planned,
            "tool_results": [*planned.get("tool_results", []), *graph_outputs],
            "executed_tool_calls": list(dict.fromkeys([
                *planned.get("executed_tool_calls", []),
                self._tool_signature(graph_result["name"], graph_result["arguments"]),
            ])),
        }

    async def _mandatory_saved_knowledge_graph_read(self, state):
        """Issue one bounded tenant-scoped graph read; never accept model-written Cypher."""
        from backend.app.core.principal import current_principal

        question = state["ticket"]["summary"]
        concepts = extract_entities([{"text": question}])
        if not concepts:
            concepts = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", question)
        identifiers = re.findall(r"\b[A-Za-z]+\d+[A-Za-z0-9_-]*\b", question)
        variants = [re.sub(r"(?<=\D)(?=\d)", " ", value) for value in identifiers]
        terms = list(dict.fromkeys(
            value.casefold()[:120] for value in [*variants, *identifiers, *concepts]
        ))[:10]
        allowed = self.harness.allowed_names("knowledge")
        name = "neo4j__read-cypher" if "neo4j__read-cypher" in allowed else "search_graph"
        if name == "neo4j__read-cypher":
            query = (
                "MATCH (d:Document) WHERE d.tenant_id = $tenant_id "
                "AND any(term IN $terms WHERE toLower(coalesce(d.title,'')) CONTAINS term "
                "OR toLower(coalesce(d.summary,'')) CONTAINS term) "
                "OPTIONAL MATCH (d)-[:HAS_PAGE]->(p:Page) "
                "OPTIONAL MATCH (p)-[:HAS_CHUNK|HAS_TABLE|HAS_PARAGRAPH|HAS_FIGURE|HAS_TABLECELL]->(o) "
                "OPTIONAL MATCH (d)<-[:MENTIONED_IN]-(m:GraphMention) "
                "OPTIONAL MATCH (m)-[:REFERS_TO]->(e:Entity) "
                "OPTIONAL MATCH (m)-[:SUPPORTED_BY]->(mo) "
                "OPTIONAL MATCH (d)<-[:DOCUMENTED_IN]-(c:GraphClaim) "
                "OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(co) "
                "RETURN coalesce(d.original_id,d.id) AS document_id,d.title AS title,"
                "d.summary AS summary,collect(DISTINCT e.name)[0..10] AS entities,"
                "[value IN (collect(DISTINCT coalesce(o.original_id,o.id)) + "
                "collect(DISTINCT coalesce(mo.original_id,mo.id)) + "
                "collect(DISTINCT coalesce(co.original_id,co.id))) WHERE value IS NOT NULL][0..500] "
                "AS object_ids LIMIT 20"
            )
            arguments = {
                "query": query,
                "params": {
                    "tenant_id": current_principal(self.settings).tenant_id,
                    "terms": terms,
                },
            }
        else:
            arguments = {"entities": terms, "limit": 20}
        try:
            output = await self._call(state, name, arguments)
        except (MCPToolError, ValueError) as exc:
            output = {
                "tool": name,
                "source_system": "tool_error",
                "timestamp": now(),
                "reliability": 0,
                "status": "unavailable",
                "error": str(exc),
                "items": [],
            }
        hydrated_output = None
        if name == "neo4j__read-cypher" and output.get("status") == "ok":
            object_ids = list(dict.fromkeys(
                str(object_id)
                for row in output.get("items", [])
                for object_id in row.get("object_ids", [])
                if object_id
            ))[:500]
            if object_ids:
                from backend.app.services.local_documents import LocalDocumentRepository

                repository = LocalDocumentRepository(self.settings)
                query_embedding = repository.embeddings.embed_texts([question])[0]
                hydrated = await asyncio.to_thread(
                    repository.search_store.resolve_object_ids,
                    object_ids,
                    query_embedding,
                )
                if hydrated:
                    for item in hydrated:
                        item["graph_enrichment"] = "available"
                        item["match_type"] = item.get("match_type", "graph_reference")
                    hydrated_output = {
                        "tool": "search_knowledge",
                        "status": "ok",
                        "items": hydrated,
                        "timestamp": now(),
                        "source_system": "chroma",
                        "reliability": 0.9,
                    }
        return {
            "name": name,
            "arguments": arguments,
            "output": output,
            "hydrated_output": hydrated_output,
        }

    @staticmethod
    def _tool_signature(name, arguments):
        return json.dumps(
            {"name": name, "arguments": arguments},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def _evidence(self, state, name, result):
        if not isinstance(result, dict) or "items" not in result:
            rows = result if isinstance(result, list) else [result] if result else []
            result = {
                "items": rows,
                "timestamp": now(),
                "source_system": "public_web"
                if name in {"search_web", "search_news", "fetch_web_page"}
                else "capability",
                "reliability": 0.6,
            }
        rows = result["items"]
        result.setdefault("timestamp", now())
        result.setdefault(
            "source_system",
            "public_web"
            if name in {"search_web", "search_news", "fetch_web_page"}
            else "capability",
        )
        result.setdefault("reliability", 0.6)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            rows = [{"error": "Capability returned a malformed result"}]
            result = {
                **result,
                "items": rows,
                "source_system": "tool_error",
                "status": "error",
                "reliability": 0,
                "timestamp": result.get("timestamp", now()),
            }
        failed = result.get("status") in {"error", "unavailable"}
        if failed:
            rows = [{"error": result.get("error") or "Capability returned no usable response"}]
        items = []
        for row in rows:
            meta = row.get("metadata", {})
            system = row.get("source_system") or result["source_system"]
            stamp = row.get("observed_at") or meta.get("created_at") or result["timestamp"]
            kind = (
                "tool_error"
                if failed or system == "tool_error" or row.get("available") is False
                else "confirmed_fact"
                if name in {"search_memory", "remember_fact"}
                else "public_documentation"
                if system in {
                    "public_web", "open_meteo", "openstreetmap", "osrm",
                    "frankfurter", "nager_date",
                } or meta.get("source_type") == "web"
                else "document"
                if name == "search_knowledge"
                else "system_record"
            )
            eid = sha256((name + str(row)).encode()).hexdigest()[:20]
            items.append(
                EvidenceItem(
                    id=eid,
                    source_system=system,
                    source_type=kind,
                    tool=name,
                    timestamp=stamp,
                    observed_at=stamp,
                    source=(
                        row.get("source") or meta.get("source")
                        or row.get("url") or row.get("source_url") or name
                    ),
                    content=row,
                    entities=state.get("analysis", {}).get("entities", []),
                    reliability=0 if kind == "tool_error" else result["reliability"],
                    relevance=self._source_relevance(name, row),
                    case_id=state["case_id"] if state.get("persist_case") is not False else None,
                    url=row.get("url") or row.get("source_url") or meta.get("url") or None,
                    doc_id=meta.get("doc_id"),
                    source_quality=(
                        "unclassified" if kind == "tool_error" else
                        quality(
                            row.get("url") or row.get("source_url") or meta.get("url") or "",
                            self.settings,
                        )
                        if kind == "public_documentation"
                        else "fixture" if system == "local_fixture" else "internal"
                    ),
                ).model_dump()
            )
        return items

    @staticmethod
    def _source_relevance(name, row):
        """Retain source semantics: keyword scores rise, vector distances fall."""
        if name != "search_knowledge":
            return 1.0
        score = max(float(row.get("score") or 0), 0)
        match_type = row.get("match_type")
        if match_type in {"keyword", "exact_identifier"}:
            return 1.0 if match_type == "exact_identifier" else min(1.0, score / 3.0)
        return 1.0 / (1.0 + score)

    async def _grade_document_evidence(self, state, candidates):
        if not candidates:
            return {"accepted": [], "rejected": [], "unverified": []}
        question = state["ticket"]["summary"]
        normalized_question = re.sub(r"[^a-z0-9]", "", question.casefold())
        query_terms = {
            term for term in re.findall(r"[a-z0-9]+", question.casefold())
            if len(term) >= 3 and term not in {"the", "from", "what", "whats", "which", "with", "my"}
        }
        strong_matches = []
        for item in candidates:
            content = item["content"]
            title = str(content.get("title") or content.get("metadata", {}).get("title") or "")
            title_stem = title.rsplit(".", 1)[0]
            normalized_title = re.sub(r"[^a-z0-9]", "", title_stem.casefold())
            text_terms = set(re.findall(r"[a-z0-9]+", str(content.get("text") or "").casefold()))
            if normalized_title and normalized_title in normalized_question and query_terms & text_terms:
                strong_matches.append(item)
        if strong_matches:
            strong_ids = {item["id"] for item in strong_matches}
            return {
                "accepted": strong_matches,
                "rejected": [
                    {"evidence": item, "reason": "A stronger title-scoped passage matched the question."}
                    for item in candidates if item["id"] not in strong_ids
                ],
                "unverified": [],
            }
        candidate_ids = [
            str(item["content"].get("id")
                or item["content"].get("metadata", {}).get("chunk_id")
                or item["id"])
            for item in candidates
        ]
        if len(candidate_ids) != len(set(candidate_ids)):
            await emit(
                "evidence_grading", "failed",
                "Model grading unavailable; retaining "
                f"{len(candidates)} retrieved documents as unverified candidates",
            )
            return {"accepted": [], "rejected": [], "unverified": candidates}
        bounded = [
            {
                "id": candidate_ids[index],
                "chunk_id": candidate_ids[index],
                "title": item["content"].get("title")
                or item["content"].get("metadata", {}).get("title"),
                "excerpt": relevant_excerpt(
                    str(item["content"].get("text", "")), state["ticket"]["summary"], 900
                ),
            }
            for index, item in enumerate(candidates)
        ]
        try:
            grade = await self.provider.generate_structured(
                "grade_evidence",
                {"question": state["ticket"]["summary"], "candidates": bounded},
                EvidenceGradeResult,
            )
            actual = set(candidate_ids)
            accepted = set(grade.accepted_ids)
            rejected = {item.id for item in grade.rejected}
            if (
                len(grade.accepted_ids) != len(accepted)
                or len(grade.rejected) != len(rejected)
                or not accepted <= actual
                or not rejected <= actual
                or accepted & rejected
                or accepted | rejected != actual
            ):
                raise ValueError("Evidence grader returned invalid candidate identifiers")
            await emit(
                "evidence_grading",
                "completed",
                f"Document relevance checked: {len(accepted)} accepted; {len(rejected)} rejected",
                findings=[f"Rejected {item.id}: {item.reason}" for item in grade.rejected[:6]],
            )
            return {
                "accepted": [
                    item for candidate_id, item in zip(candidate_ids, candidates)
                    if candidate_id in accepted
                ],
                "rejected": [
                    {
                        "evidence": item,
                        "reason": next(
                            rejected_item.reason
                            for rejected_item in grade.rejected
                            if rejected_item.id == candidate_id
                        ),
                    }
                    for candidate_id, item in zip(candidate_ids, candidates)
                    if candidate_id in rejected
                ],
                "unverified": [],
            }
        except Exception:
            await emit(
                "evidence_grading",
                "failed",
                "Model grading unavailable; retaining "
                f"{len(candidates)} retrieved documents as unverified candidates",
            )
            return {"accepted": [], "rejected": [], "unverified": candidates}

    async def observe(self, state):
        evidence = list(state.get("evidence", []))
        retrieval_candidates = list(state.get("retrieval_candidates", []))
        seen = {e["id"] for e in evidence}
        candidate_map = {e["id"]: e for e in retrieval_candidates}
        converted = []
        for item in state.get("tool_results", []):
            rows = self._evidence(state, item["name"], item["output"])
            if item["name"] == "search_knowledge":
                errors = [row for row in rows if row["source_type"] == "tool_error"]
                assessment = await self._grade_document_evidence(
                    state, [row for row in rows if row["source_type"] == "document"]
                )
                for row in assessment["accepted"]:
                    candidate_map[row["id"]] = {**row, "assessment": "accepted"}
                for rejected in assessment["rejected"]:
                    row = rejected["evidence"]
                    candidate_map[row["id"]] = {
                        **row,
                        "assessment": "rejected",
                        "assessment_reason": rejected["reason"],
                    }
                for row in assessment["unverified"]:
                    candidate_map[row["id"]] = {**row, "assessment": "unverified"}
                await emit(
                    "source_assessment",
                    "completed" if not assessment["unverified"] else "failed",
                    "Document candidates assessed: "
                    f"{len(assessment['accepted'])} accepted, "
                    f"{len(assessment['rejected'])} rejected, "
                    f"{len(assessment['unverified'])} unverified",
                    accepted_count=len(assessment["accepted"]),
                    rejected_count=len(assessment["rejected"]),
                    unverified_count=len(assessment["unverified"]),
                )
                rows = errors + assessment["accepted"]
            converted.extend(rows)
        retrieval_candidates = list(candidate_map.values())[-24:]
        for row in converted:
            if row["id"] not in seen:
                evidence.append(row)
                seen.add(row["id"])
        contradictions = list(state.get("contradictions", []))
        by_predicate = {}
        trusted_facts = []
        for item in evidence:
            row = item["content"]
            if item["tool"] == "get_user_context" and row.get("name"):
                for predicate, field in (("works_at", "company"), ("current_site", "current_site")):
                    if row.get(field):
                        trusted_facts.append(
                            {
                                "subject": row["name"],
                                "predicate": predicate,
                                "value": row[field],
                                "source": "trusted_tool",
                                "observed_at": item["timestamp"],
                            }
                        )
        for fact in (
            state.get("remembered_facts", []) + trusted_facts + state.get("confirmed_facts", [])
        ):
            key = (fact["subject"], fact["predicate"])
            old = by_predicate.get(key)
            if old and old["value"] != fact["value"]:
                message = f"Conflicting {key[1]} for {key[0]}: {old['value']} ({old['source']}, {old['observed_at']}) versus {fact['value']} ({fact['source']}, {fact['observed_at']}). Evaluate freshness before reuse."
                if message not in contradictions:
                    contradictions.append(message)
            by_predicate[key] = fact
        await self.note(
            state,
            "observe",
            f"Evidence correlated: {len(evidence)} items; {len(contradictions)} contradictions",
        )
        public_sources = [
            {
                "title": str(item["content"].get("title") or item["source"])[:200],
                "url": item.get("url"),
            }
            for item in evidence
            if item.get("url") and item["source_type"] == "public_documentation"
        ][-6:]
        retrieved = self.usable_evidence({"evidence": evidence})
        discovery_leads = sum(
            item["tool"] in {"search_web", "search_news"} for item in evidence
        )
        tool_errors = sum(item["source_type"] == "tool_error" for item in evidence)
        await emit(
            "evidence_collected",
            "completed",
            f"Evidence reviewed: {len(retrieved)} retrieved source records, "
            f"{discovery_leads} search leads, {tool_errors} errors; "
            f"{len(contradictions)} contradictions",
            sources=public_sources,
            accepted_document_count=sum(
                item.get("assessment") == "accepted" for item in retrieval_candidates
            ),
            rejected_document_count=sum(
                item.get("assessment") == "rejected" for item in retrieval_candidates
            ),
            unverified_document_count=sum(
                item.get("assessment") == "unverified" for item in retrieval_candidates
            ),
        )
        return {
            "evidence": evidence,
            "retrieval_candidates": retrieval_candidates,
            "contradictions": contradictions,
            "tool_results": [],
        }

    async def reason(self, state):
        iteration = state.get("research_iteration", 0) + 1
        context = self.context(state, True)
        if state.get("decision_issues"):
            # An erroneous critic can repeat the invalid premise it was asked
            # to judge. Application validation takes priority during revision.
            context.pop("verification", None)
        for attempt in range(2):
            decision = await self.provider.generate_structured(
                "reason", context, InvestigationDecision
            )
            try:
                self._validate_evidence_refs(
                    state, [identifier for item in decision.hypotheses for identifier in item.evidence_ids]
                )
                clarification = str(decision.question_for_human or "")
                page_match = re.search(r"\bpage\s+(\d+)\b", clarification, re.IGNORECASE)
                location_request = bool(re.search(
                    r"\b(?:which|what)\s+(?:section|page)|\bsurrounding\s+text\b|\bexact\s+location\b",
                    clarification, re.IGNORECASE,
                ))
                value_request = bool(re.search(
                    r"\b(?:what|which)\b.{0,100}\b(?:code|number|identifier|date|amount|value)\b",
                    clarification, re.IGNORECASE,
                ))
                if state.get("workflow") == "knowledge" and (
                    page_match or location_request or value_request
                ):
                    requested_page = int(page_match.group(1)) if page_match else None
                    present = [
                        item for item in self.usable_evidence(state)
                        if item.get("source_type") == "document"
                        and (
                            requested_page is None
                            or item.get("content", {}).get("metadata", {}).get("page") == requested_page
                        )
                        and item.get("content", {}).get("metadata", {}).get("page") is not None
                        and str(item.get("content", {}).get("text") or "").strip()
                    ]
                    if present:
                        clarification_terms = {
                            token.casefold()
                            for token in re.findall(r"[A-Za-z0-9]+", clarification)
                            if len(token) >= 3
                            and token.casefold() not in {
                                "what", "which", "the", "for", "from", "listed",
                                "document", "program", "referring", "provide",
                            }
                        }
                        present.sort(
                            key=lambda item: sum(
                                term in str(item["content"].get("text") or "").casefold()
                                for term in clarification_terms
                            ),
                            reverse=True,
                        )
                        matching = present
                        if value_request:
                            matching = [
                                item for item in present
                                if sum(
                                    term in str(item["content"].get("text") or "").casefold()
                                    for term in clarification_terms
                                ) >= min(2, len(clarification_terms))
                                and re.search(
                                    r"(?:\b\d[\w.-]*\b|:\s*\S+)",
                                    str(item["content"].get("text") or ""),
                                )
                            ]
                        if matching and attempt == 0:
                            available_pages = sorted({
                                item["content"]["metadata"]["page"] for item in matching
                            })
                            context["decision_validation_feedback"] = {
                                "message": (
                                    "Do not ask the user for a document value, text, or location that is "
                                    "already present in accepted evidence. The text and page "
                                    f"provenance are already available on page(s) {available_pages}. "
                                    "Answer from this evidence or identify a different genuine gap."
                                ),
                                "matching_evidence": [
                                    {
                                        "id": item["id"],
                                        "title": item["content"].get("title")
                                        or item["content"].get("metadata", {}).get("title"),
                                        "page": item["content"]["metadata"]["page"],
                                        "excerpt": relevant_excerpt(
                                            str(item["content"].get("text") or ""),
                                            state["ticket"]["summary"], 1600,
                                        ),
                                    }
                                    for item in matching[:3]
                                ],
                            }
                            continue
                        if matching:
                            raise LLMError(
                                "Reasoning repeatedly requested document evidence that is already present"
                            )
                break
            except LLMError as exc:
                if attempt:
                    permitted = {e["id"] for e in state.get("evidence", [])}
                    decision = decision.model_copy(update={
                        "outcome": "cannot_verify",
                        "hypotheses": [
                            item.model_copy(update={
                                "evidence_ids": [identifier for identifier in item.evidence_ids if identifier in permitted],
                                "status": "plausible",
                            })
                            for item in decision.hypotheses
                        ],
                        "answer": None,
                        "enough_information": False,
                        "confidence": 0,
                        "uncertainty": list(dict.fromkeys([
                            *decision.uncertainty,
                            "The proposed answer could not be tied to available evidence.",
                        ])),
                    })
                    await self._emit_validation_failure({
                        "request_id": state.get("case_id"),
                        "provider": self.provider.name(),
                        "stage": "reason",
                        "validation_error_code": "UNKNOWN_EVIDENCE_REFERENCE",
                        "expected_argument_names": sorted(
                            e["id"] for e in state.get("evidence", [])
                        ),
                        "actual_argument_names": sorted(exc.diagnostics.get("invalid_evidence_ids", [])),
                        "attempt_number": attempt + 1,
                    })
                    break
                permitted = sorted(e["id"] for e in state.get("evidence", []))
                context["reason_validation_feedback"] = (
                    "Remove these invalid evidence IDs: "
                    + ", ".join(exc.diagnostics.get("invalid_evidence_ids", []))
                    + ". Permitted evidence IDs are: "
                    + (", ".join(permitted) if permitted else "none")
                    + ". Cite only exact permitted IDs or leave evidence_ids empty."
                )
        # Carry forward the planner's grounded clarification decision. A
        # retrieval-free clarification must not become an unsupported answer or
        # another generic search merely because the model forgot its own plan.
        plan = state.get("plan") or {}
        answered_questions = {
            str(item.get("question") or "").strip().casefold()
            for item in state.get("human_answers", [])
            if item.get("question")
        }
        if (
            state.get("workflow") == "knowledge"
            and plan.get("clarification_needed")
            and not plan.get("tool_calls")
            and plan.get("clarification_question")
            and plan["clarification_question"].strip().casefold() not in answered_questions
        ):
            decision.outcome = "ask_one_question"
            decision.question_for_human = plan["clarification_question"]
            decision.human_field = "clarification_context"
            decision.question_reason = (
                plan.get("clarification_reason")
                or "The requested reference cannot be resolved from this conversation."
            )
            decision.answer = None
            decision.enough_information = False
            decision.needs_web_research = False
            decision.requested_tools = []
            decision.confidence = 0
        summary = state["ticket"]["summary"].casefold()
        terse_diagnostic_request = (
            len(summary.split()) <= 8
            and (
                len(summary.split()) <= 3
                or any(word in summary for word in ("vpn", "wifi", "wireless", "wirless"))
            )
        )
        diagnostic_evidence = [
            item for item in self.usable_evidence(state)
            if item["tool"] not in {
                "get_user_context", "get_company_context", "search_memory",
                "search_remembered_facts",
            }
        ]
        if (
            state["workflow"] == "service_desk"
            and not diagnostic_evidence
            and terse_diagnostic_request
        ):
            # An underspecified incident cannot be diagnosed by broad web
            # searching. Ask for one discriminating observation first.
            if "vpn" in summary:
                question = "What exact error message or code appears when the VPN connection fails?"
                field = "vpn_error_message"
            elif any(word in summary for word in ("wifi", "wireless", "wirless")):
                question = "What exact message appears when the wireless connection drops or reconnects?"
                field = "wireless_error_message"
            else:
                question = "Which application or service is affected, and what exact error or symptom do you see?"
                field = "affected_service_and_symptom"
            decision.question_for_human = question
            decision.human_field = field
            decision.question_reason = (
                "This observation materially narrows the next safe diagnostic step."
            )
            decision.needs_web_research = False
            decision.research_queries = []
            decision.enough_information = False
            decision.confidence = 0
        issues = []
        if (
            decision.question_for_human
            and decision.question_for_human.strip().casefold() in answered_questions
        ):
            issues.append(
                "The proposed clarification was already answered. Use the confirmed human fact "
                "and do not ask the same question again."
            )
            decision.question_for_human = None
            decision.human_field = None
            decision.question_subject = None
            decision.question_reason = None
        if state["workflow"] == "knowledge" and decision.human_field in {
            "current_site",
            "required_update_installed",
        }:
            issues.append("A ticket-specific question is not permitted for this knowledge request.")
        if decision.human_field == "current_site":
            product = (state.get("analysis", {}).get("product") or "").casefold()
            product_terms = set(re.findall(r"\w+", product))
            relevant_sites = [
                e
                for e in state.get("evidence", [])
                if e["tool"] in {"get_active_alerts", "get_recent_changes", "get_site_context"}
                and e["content"].get("site")
                and (
                    not product_terms
                    or product_terms
                    & set(re.findall(r"\w+", str(e["content"].get("product", "")).casefold()))
                )
            ]
            if not relevant_sites:
                issues.append(
                    "Location question lacks materially site-specific evidence relevant to this issue. Choose an identity/error/device clarification instead when needed."
                )
        if decision.question_for_human and not decision.question_reason:
            issues.append("Human question lacks a material decision justification.")
        if not self.usable_evidence(state) and not self._direct_model_answer(state):
            decision.enough_information = False
            decision.confidence = 0
        executed = set(state.get("executed_tool_calls", []))
        requested_tools = [
            call
            for call in decision.requested_tools
            if self._tool_signature(call.name, call.arguments) not in executed
        ]
        if state.get("explicit_saved_knowledge"):
            requested_tools = [
                call for call in requested_tools
                if call.name not in {"search_web", "search_news", "fetch_web_page"}
            ]
            decision.needs_web_research = False
            decision.research_queries = []
        next_plan = None
        if requested_tools:
            next_plan = InvestigationPlan(
                goal=decision.recommended_next_action or "Resolve the remaining evidence gaps",
                additional_evidence_useful=True,
                source_strategy=decision.decision_justification,
                known_facts=decision.inferences,
                unknowns=[item.description for item in decision.missing_information],
                tool_calls=requested_tools,
                stop_condition="Stop when the requested observations resolve the stated evidence gaps.",
            )
        await self.note(
            state,
            "reason",
            decision.decision_justification or decision.evidence_summary,
            self.provider.name(),
        )
        await emit(
            "evidence_evaluation",
            "completed",
            "Assessment selected: " + (
                "collect additional evidence" if requested_tools or decision.needs_web_research
                else "ask for clarification" if decision.question_for_human
                else "prepare a supported answer" if decision.enough_information and decision.answer
                else "report remaining evidence gap"
            ),
            rationale=(decision.decision_justification or decision.evidence_summary)[:600],
            actions=[
                {"tool": call.name, "reason": call.reason[:300]}
                for call in requested_tools
            ],
            research_queries=decision.research_queries,
            missing_information=[item.description for item in decision.missing_information[:4]],
            next_action=(
                decision.question_for_human
                or ("Research the remaining evidence gaps" if decision.needs_web_research else None)
                or decision.recommended_next_action
                or ("Verify the candidate answer" if decision.enough_information else "Revise the investigation plan")
            ),
        )
        result = {
            "decision": decision.model_dump(),
            "decision_issues": issues,
            "research_iteration": iteration,
            "hypotheses": [h.model_dump() for h in decision.hypotheses],
            "inferences": decision.inferences,
            "missing_information": [m.model_dump() for m in decision.missing_information],
            "confidence": decision.confidence,
            "uncertainty": decision.uncertainty,
            "likely_issue": decision.likely_issue,
            "recommended_next_action": decision.recommended_next_action,
            "pending_question": decision.question_for_human,
            "pending_field": decision.human_field,
            "status": "needs_input" if decision.question_for_human else "investigating",
        }
        if next_plan is not None:
            result.update(plan=next_plan.model_dump(), plan_executed=False)
        return result

    @staticmethod
    def usable_evidence(state):
        return [
            e
            for e in state.get("evidence", [])
            if e["source_type"] != "tool_error"
            and e["tool"] not in {
                "list_available_context", "search_web", "search_news", "user_request"
            }
            and not e["content"].get("available") is False
        ]

    @staticmethod
    def _direct_model_answer(state):
        plan = state.get("plan") or {}
        return (
            state.get("workflow") == "knowledge"
            and state.get("intent", {}).get("knowledge_source") == "general"
            and not plan.get("tool_calls")
            and plan.get("additional_evidence_useful") is False
        )

    def route(self, state):
        if state["research_iteration"] >= self.settings.max_investigation_cycles:
            return "finalize"
        if not state.get("plan_executed", True) and state.get("plan", {}).get("tool_calls"):
            return "execute_tools"
        d = state["decision"]
        if (
            d.get("needs_web_research")
            and state.get("research_rounds", 0) < self.settings.max_research_rounds
        ):
            return "research"
        return "verify"

    async def ask_human(self, state):
        # The request event has a stable key, so interrupt replay cannot duplicate it.
        await emit(
            "follow_up",
            "waiting",
            "A specific answer is needed to continue",
            question=state["pending_question"],
            rationale=state["decision"].get("question_reason"),
            next_action="Resume this saved investigation after the answer is submitted",
        )
        await self.note(
            state,
            "human_input_requested",
            state["pending_question"],
            "langgraph",
            event_key=f"human-question:{state.get('research_iteration')}:{state['pending_field']}",
        )
        answer = str(
            interrupt(
                {
                    "question": state["pending_question"],
                    "field": state["pending_field"],
                    "case_id": state["case_id"],
                }
            )
        ).strip()
        if not answer:
            raise ValueError("Human answer cannot be empty")
        field = state["pending_field"]
        subject = (
            state["decision"].get("question_subject")
            or state["analysis"].get("user_name")
            or state["case_id"]
        )
        value = answer
        if field == "current_site" and state["analysis"].get("company"):
            for site in state.get("available_context", {}).get("sites", []):
                if answer.casefold() in site.casefold():
                    value = site
            if answer.casefold() in {"2nd floor", "second floor"}:
                value = state["analysis"]["company"] + " 2nd floor"
        fact = MemoryFact(
            subject=subject,
            predicate=field,
            value=value,
            source="human_confirmed",
            source_system="human_input",
            observed_at=now(),
            confidence=1,
            case_id=state["case_id"],
            metadata={"question": state["pending_question"]},
        )
        if state.get("persist_case") is not False:
            await self._call(state, "remember_fact", {"fact": fact.model_dump()})
        await self.note(
            state, "human_response", f"Human response received: {answer}", "human_input"
        )
        await self.note(state, "resume", "Investigation resumed from saved checkpoint", "langgraph")
        item = EvidenceItem(
            id=sha256((state["case_id"] + field + answer).encode()).hexdigest()[:20],
            source_system="human_input",
            source_type="human_confirmation",
            tool="ask_human",
            timestamp=fact.observed_at,
            observed_at=fact.observed_at,
            source="human_confirmed",
            content=fact.model_dump(),
            entities=[subject],
            reliability=1,
            case_id=state["case_id"],
        )
        return {
            "confirmed_facts": state.get("confirmed_facts", []) + [fact.model_dump()],
            "evidence": state.get("evidence", []) + [item.model_dump()],
            "human_answers": state.get("human_answers", [])
            + [{"question": state["pending_question"], "answer": answer}],
            "status": "investigating",
            "pending_question": None,
            "pending_field": None,
            "plan_executed": True,
            "research_iteration": 0,
            "decision_issues": [],
            "verification": {},
        }

    async def research(self, state):
        decision = state.get("decision") or {}
        public_request = state.get("intent", {}).get("knowledge_source") == "public_web"
        queries = decision.get("research_queries") or (
            [state["intent"]["goal"]] if public_request else []
        )
        if not queries:
            raise ValueError(
                "Public research requires a model-formulated query tied to an evidence gap"
            )
        outputs = []
        search_tool = (
            "search_news"
            if public_request and state["intent"].get("public_topic") == "news"
            else "search_web"
        )

        async def failed(name, error):
            outputs.append(
                {
                    "name": name,
                    "output": {
                        "tool": name,
                        "source_system": "tool_error",
                        "timestamp": now(),
                        "reliability": 0,
                        "status": "error",
                        "error": str(error),
                        "items": [],
                    },
                }
            )
            await self.note(state, name, "Public research capability unavailable", "tool_error")

        for query in queries[:2]:
            await emit(
                "research_query",
                "running",
                "Searching the web for a specific evidence gap",
                query=query[:500],
                rationale=(decision.get("decision_justification") or decision.get("evidence_summary", ""))[:500],
                event_id=f"research:{state.get('research_rounds', 0)}:{sha256(query.encode()).hexdigest()[:12]}",
            )
            try:
                arguments = {"query": query, "max_results": 5}
                if search_tool == "search_news":
                    arguments["timelimit"] = "w"
                search = await self._call(state, search_tool, arguments)
            except MCPToolError as exc:
                await failed(search_tool, exc)
                continue
            outputs.append({"name": search_tool, "output": search})
            rows = (
                search.get("items", [])
                if isinstance(search, dict) and "items" in search
                else search
            )
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                await failed(search_tool, MCPToolError("Public search returned malformed results"))
                continue
            raw_result_count = len(rows)
            rows = rank_sources(rows, self.settings)
            if not rows:
                # A source-policy filter is different from an empty search.
                # Surface the actual blocker instead of claiming no results.
                policy_rejected = raw_result_count > 0
                reason = (
                    f"Web search returned {raw_result_count} candidate(s), but "
                    f"RESEARCH_SOURCE_POLICY={self.settings.research_source_policy} "
                    "excluded all URLs. An operator can set prefer_primary "
                    "to consider third-party sources while prioritizing configured primary domains."
                    if policy_rejected else
                    "Public search returned no usable candidates for this query."
                )
                await emit(
                    "source_policy_filter",
                    "completed",
                    reason,
                    result_count=raw_result_count,
                    policy=self.settings.research_source_policy,
                )
                await failed(search_tool, MCPToolError(reason))
                continue
            selection = await self.provider.generate_structured(
                "evaluate_sources",
                {
                    "ticket": state["ticket"],
                    "request": state["ticket"]["summary"],
                    "query": query,
                    "results": rows,
                    "preferred_domains": self.settings.research_preferred_domains,
                    "source_policy": self.settings.research_source_policy,
                    "missing_evidence": decision.get("missing_information", []),
                    "selection_phase": "candidate_discovery",
                },
                ResearchSelection,
            )
            available = {r["url"]: r for r in rows}
            selected_urls = [url for url in selection.selected_urls if url in available]
            if len(selected_urls) != len(selection.selected_urls):
                await self.note(
                    state,
                    "evaluate_sources",
                    "Ignored a source URL that was not present in the search results",
                    "application_validation",
                )
            await self.note(
                state, "evaluate_sources", selection.justification, self.provider.name()
            )
            await emit(
                "source_selection", "completed",
                f"Selected {len(selected_urls)} public article(s) for verification",
                result_count=len(selected_urls),
                rationale=selection.justification[:500],
                sources=[{"title": available[url].get("title") or url, "url": url}
                         for url in selected_urls],
            )
            for url in selected_urls:
                hit = available[url]
                capability_name = "fetch_web_page"
                try:
                    fetched = await self._call(state, "fetch_web_page", {"url": hit["url"]})
                    if not isinstance(fetched, dict) or fetched.get("status") != "ok":
                        await failed(capability_name, MCPToolError(
                            fetched.get("error", "Webpage fetch failed") if isinstance(fetched, dict)
                            else "Invalid webpage response"
                        ))
                        continue
                    page = fetched.get("result")
                    if not isinstance(page, dict):
                        await failed(capability_name, MCPToolError("Invalid fetched article"))
                        continue
                    if not permits(page.get("url", ""), self.settings):
                        continue
                    page["source_quality"] = quality(page.get("url", ""), self.settings)
                    for key in ("published_at", "publisher"):
                        if hit.get(key) is not None:
                            page[key] = hit[key]
                    text = page.get("text", "")
                    if len(text.strip()) < 120:
                        continue
                    # Reasoning and verification assess actual article content;
                    # a lexical substring test can reject valid news in other languages.
                    verified_page = {**page, "text": relevant_excerpt(text, query, 6000)}
                    outputs.append({"name": "fetch_web_page", "output": {
                        **fetched, "items": [verified_page], "result": verified_page}})
                    await emit(
                        "source_verified",
                        "completed",
                        f"Relevant source collected: {str(page.get('title') or urlsplit(page['url']).hostname)[:220]}",
                        rationale=selection.justification[:500],
                        sources=[{"title": str(page.get("title") or page["url"])[:200], "url": page["url"]}],
                    )
                except (MCPToolError, ValueError) as exc:
                    await failed(capability_name, exc)
        return {"tool_results": outputs, "research_rounds": state.get("research_rounds", 0) + 1,
                "web_pages_fetched": state.get("web_pages_fetched", 0)}

    def _validate_evidence_refs(self, state, ids):
        invalid = sorted(set(ids) - {e["id"] for e in state.get("evidence", [])})
        if invalid:
            raise LLMError(
                "Structured reasoning cited unknown evidence identifiers",
                category="application_validation",
                request_id=state.get("case_id"),
                diagnostics={"invalid_evidence_ids": invalid},
            )

    async def verify(self, state):
        # Judge the current candidate independently; prior critic text and
        # preliminary analysis hypotheses can otherwise anchor the review.
        context = self.context(state)
        context = {
            key: context[key]
            for key in (
                "ticket",
                "workflow",
                "decision",
                "evidence",
                "confirmed_facts",
                "remembered_facts",
                "human_answers",
                "contradictions",
                "conversation",
            )
        }
        context["direct_model_answer"] = self._direct_model_answer(state)
        clarifying = bool(state["decision"].get("question_for_human"))
        if clarifying:
            # Verify the question and its premises before interrupting. A
            # speculative diagnosis is not a prerequisite for asking a safe
            # clarification, and remains withheld until full assessment review.
            context["verification_target"] = "clarification"
            context["decision"] = {
                **state["decision"],
                "hypotheses": [], "inferences": [], "likely_issue": None, "answer": None,
                "evidence_summary": state["decision"].get("question_reason")
                or "Review the proposed clarification.",
                "recommended_next_action": state["decision"]["question_for_human"],
            }
        verification = await self.provider.generate_structured(
            "verify", context, VerificationResult
        )
        # For clarification verification, the user has not supplied the missing
        # fact. Missing answer evidence is not an unsupported *question*.
        # Keep the verifier's explicit invalid-question finding, if any.
        if clarifying and verification.clarification_valid is True:
            verification.unsupported_claims = []
            verification.should_continue_research = False
        elif not clarifying and verification.unsupported_claims:
            # Unsupported claims and a terminal/no-research flag are
            # contradictory. Normalize the contract so the bounded recovery
            # edge can revise the rejected candidate once.
            verification.should_continue_research = True
        self._validate_evidence_refs(state, verification.evidence_ids)
        if state.get("decision_issues"):
            verification.unsupported_claims = list(
                dict.fromkeys(verification.unsupported_claims + state["decision_issues"])
            )
            verification.should_continue_research = True
        if (
            not self.usable_evidence(state)
            and not self._direct_model_answer(state)
            and not state["decision"].get("question_for_human")
        ):
            verification.confidence = 0
            if "No supporting evidence is available." not in verification.unsupported_claims:
                verification.unsupported_claims.append("No supporting evidence is available.")
            verification.should_continue_research = True
        await self.note(state, "verify", verification.justification, self.provider.name())
        await emit(
            "verification_summary",
            "completed",
            "Verification found unresolved claims"
            if verification.unsupported_claims
            else "Verification accepted the candidate assessment",
            rationale=verification.justification[:600],
            supported_count=len(verification.supported_claims),
            unsupported_count=len(verification.unsupported_claims),
            next_action=(
                "Plan a different authorized lookup for the unresolved evidence gap"
                if verification.should_continue_research
                and state.get("research_iteration", 0) < self.settings.max_investigation_cycles
                else "Finalize with the specific remaining limitation"
                if verification.unsupported_claims
                else "Finalize the supported answer"
            ),
        )
        if (
            not clarifying
            and verification.should_continue_research
            and state.get("research_iteration", 0) < self.settings.max_investigation_cycles
        ):
            await emit(
                "recovery",
                "completed",
                "Recovery required: plan a new evidence lookup",
                rationale=verification.justification[:600],
                missing_information=(
                    verification.missing_evidence + verification.unsupported_claims
                )[:4],
                next_action="Choose a new authorized source or meaningfully different query",
            )
        result = {
            "verification": verification.model_dump(),
            "confidence": min(state.get("confidence", 0), verification.confidence),
            "uncertainty": list(
                dict.fromkeys(
                    state.get("uncertainty", [])
                    + verification.missing_evidence
                    + verification.unsupported_claims
                )
            ),
            "contradictions": list(
                dict.fromkeys(state.get("contradictions", []) + verification.contradictions)
            ),
        }
        if clarifying:
            result.update(
                hypotheses=[
                    hypothesis for hypothesis in state["decision"].get("hypotheses", [])
                    if hypothesis.get("status") == "plausible" and hypothesis.get("evidence_ids")
                ],
                inferences=[], likely_issue=None,
            )
        return result

    def verification_route(self, state):
        v = state["verification"]
        decision = state["decision"]
        if decision.get("question_for_human"):
            if (
                not state.get("decision_issues")
                and not v.get("contradictions")
                and v.get("clarification_valid") is True
            ):
                return "ask_human"
            if decision.get("human_field") in {
                "vpn_error_message", "wireless_error_message", "affected_service_and_symptom"
            }:
                return "ask_human"
            if not state.get("decision_issues") and not v["contradictions"]:
                if not v["unsupported_claims"]:
                    return "ask_human"
            if state.get("research_iteration", 0) < 2:
                return "reason"
        if (
            not decision.get("question_for_human")
            and v.get("should_continue_research")
            and state.get("research_iteration", 0) < self.settings.max_investigation_cycles
        ):
            # Verification identified a material gap. Ask the model to PLAN
            # a different authorized source instead of blindly regenerating
            # the same answer against unchanged evidence. One recovery pass
            # remains bounded by research_iteration.
            return "plan"
        if (
            state.get("decision_issues")
            and state.get("research_iteration", 0) < self.settings.max_investigation_cycles
        ):
            # One bounded schema/policy correction is allowed without another
            # source lookup. This cannot become an evidence-reconsideration loop.
            return "reason"
        # Verification is terminal for this evidence pass. Only new human
        # information starts a new source-selection pass.
        return "finalize"

    async def remember(self, state):
        # Only explicit trusted record fields are eligible for reusable facts.
        for evidence in state.get("evidence", []):
            row = evidence["content"]
            if evidence["tool"] == "get_user_context" and row.get("name") and row.get("company"):
                fact = MemoryFact(
                    subject=row["name"],
                    predicate="works_at",
                    value=row["company"],
                    source="trusted_tool",
                    source_system=evidence["source_system"],
                    observed_at=evidence["timestamp"],
                    confidence=evidence["reliability"],
                    case_id=state["case_id"],
                    metadata={"evidence_id": evidence["id"]},
                )
                await self._call(state, "remember_fact", {"fact": fact.model_dump()})
        await self.note(
            state, "remember", "Verified assessment recorded; issue resolution remains unconfirmed"
        )
        return {}

    async def finalize(self, state):
        # Finalization is immediate after verification, but trusted context can
        # still be persisted here without another reasoning/research cycle.
        if state.get("persist_case") is not False:
            for evidence in state.get("evidence", []):
                row = evidence["content"]
                if evidence["tool"] == "get_user_context" and row.get("name") and row.get("company"):
                    fact = MemoryFact(
                        subject=row["name"], predicate="works_at", value=row["company"],
                        source="trusted_tool", source_system=evidence["source_system"],
                        observed_at=evidence["timestamp"], confidence=evidence["reliability"],
                        case_id=state["case_id"], metadata={"evidence_id": evidence["id"]},
                    )
                    await self._call(state, "remember_fact", {"fact": fact.model_dump()})
        d = InvestigationDecision.model_validate(state["decision"])
        v = state.get("verification", {})
        direct_model_answer = self._direct_model_answer(state)
        limited = state["research_iteration"] >= self.settings.max_investigation_cycles
        supported = (
            bool(v)
            and not v.get("unsupported_claims")
            and (d.enough_information or bool(d.answer))
            and (bool(self.usable_evidence(state)) or direct_model_answer)
        )
        uncertainty = list(
            dict.fromkeys(
                d.uncertainty + v.get("missing_evidence", []) + v.get("unsupported_claims", [])
            )
        )
        if limited:
            uncertainty.append(
                "Investigation cycle limit reached; additional technician evidence is required."
            )
        if not supported:
            uncertainty += [
                f"{e['tool']} unavailable: {e['content'].get('error', 'Capability failed')}"
                for e in state.get("evidence", [])
                if e["source_type"] == "tool_error"
            ]
        confidence = min(d.confidence, v.get("confidence", 0)) if supported else 0
        events = (
            list(self._request_events.get(state["case_id"], []))
            if state.get("persist_case") is False
            else (await self.harness.invoke("get_case_history", {"case_id": state["case_id"]}))[
                "items"
            ]
        )
        limitation = None
        if not supported:
            errors = [
                e["content"].get("error") for e in state.get("evidence", [])
                if e["source_type"] == "tool_error" and e["content"].get("error")
            ]
            observed_tools = {
                e["tool"] for e in state.get("evidence", [])
                if e["source_type"] != "tool_error"
            }
            if errors:
                limitation = "Some evidence sources failed: " + "; ".join(
                    dict.fromkeys(str(error) for error in errors)
                )
            elif observed_tools & {"search_web", "search_news"}:
                limitation = (
                    "Public search returned candidate results, but no verified source "
                    "established the requested fact."
                )
            elif "search_knowledge" in observed_tools or state.get("retrieval_candidates"):
                limitation = (
                    "I searched available local knowledge but could not verify the "
                    "requested fact from the relevant retrieved passages."
                )
            else:
                limitation = "I could not verify the requested fact from the evidence available."
            # A missing source or disambiguating detail should remain visible,
            # rather than reducing all unsuccessful investigations to a generic
            # "no sources" claim or silently dropping the answer.
            missing = [
                item.description for item in d.missing_information
                if item.affects_decision and item.description.strip()
            ]
            if missing:
                limitation += " I still need: " + missing[0].rstrip(".") + "."
        answer_value = (d.answer or d.recommended_next_action) if supported else limitation
        if (
            supported
            and state.get("workflow") == "knowledge"
            and answer_value
            and not re.search(r"\bpage\s+\d+\b", answer_value, re.IGNORECASE)
        ):
            answer_identifiers = {
                token for token in re.findall(r"\b[A-Za-z0-9-]{3,}\b", answer_value)
                if any(character.isdigit() for character in token)
            }
            cited_document = next((
                item for item in self.usable_evidence(state)
                if item.get("source_type") == "document"
                and item.get("content", {}).get("metadata", {}).get("page") is not None
                and any(
                    identifier.casefold() in str(item.get("content", {}).get("text") or "").casefold()
                    for identifier in answer_identifiers
                )
            ), None)
            if cited_document:
                metadata = cited_document["content"]["metadata"]
                title = cited_document["content"].get("title") or metadata.get("title") or "saved document"
                answer_value = (
                    f"{answer_value.rstrip().rstrip('.')} ({title}, page {metadata['page']})."
                )
        response = FinalResponse(
            issue_summary=state["ticket"]["summary"],
            answer=answer_value,
            likely_causes=d.hypotheses,
            confirmed_facts=state.get("confirmed_facts", []),
            inferences=d.inferences,
            contradictions=state.get("contradictions", []),
            evidence=state.get("evidence", []),
            actions_already_taken=[e["content"] for e in events if e.get("tool") != "error"],
            recommended_next_action=(
                d.recommended_next_action
                if d.recommended_next_action
                and (self.usable_evidence(state) or direct_model_answer)
                else "Collect missing evidence before accepting the candidate assessment."
                if v.get("unsupported_claims")
                else d.recommended_next_action
            ),
            troubleshooting_steps=d.troubleshooting_steps
            if not v.get("unsupported_claims")
            else [],
            questions_remaining=[m.description for m in d.missing_information],
            confidence=confidence,
            uncertainty=uncertainty,
            escalation_required=d.escalation_required or not supported,
            sources=list(
                dict.fromkeys(
                    e["url"] or e["source"]
                    for e in state.get("evidence", [])
                    if e["source_type"] != "tool_error"
                )
            ),
        )
        await self.note(
            state,
            "finalize",
            "Assessment ready"
            if supported
            else "Insufficient information; explicit uncertainty retained",
        )
        await emit(
            "final_assessment",
            "completed",
            "Final assessment is supported by verified evidence"
            if supported
            else "Final assessment retains unresolved evidence gaps",
            confidence=confidence,
            source_count=len(response.sources),
            conclusion=concise_answer_line(answer_value)
            if supported else None,
        )
        # Persist and return the complete verified answer. Only the separate
        # activity conclusion is compacted for timeline display.
        final_answer = answer_value
        response.answer = answer_value
        chat_messages = state.get("chat_messages", [])
        if state.get("persist_case") is False and response.answer:
            pair = [
                {"role": "user", "content": state["ticket"]["summary"]},
                {"role": "assistant", "content": response.answer},
            ]
            if chat_messages[-2:] != pair:
                chat_messages = [*chat_messages, *pair][-16:]
        return {
            "status": "assessment_ready" if supported else "insufficient_information",
            "confidence": confidence,
            "pending_question": None,
            "pending_field": None,
            "uncertainty": uncertainty,
            "final_response": response.model_dump(),
            "final_answer": final_answer,
            "likely_issue": d.likely_issue if not v.get("unsupported_claims") else None,
            "recommended_next_action": response.recommended_next_action,
            "actions_already_taken": response.actions_already_taken,
            "chat_messages": chat_messages,
        }
