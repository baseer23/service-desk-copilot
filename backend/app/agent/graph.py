"""Supervisor and two compiled subgraphs. Every capability invocation uses LangChain→MCP."""

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from backend.app.agent.state import InvestigationState
from backend.app.agent.models import (
    RequestIntent,
    TicketAnalysis,
    InvestigationPlan,
    InvestigationDecision,
    EvidenceItem,
    MemoryFact,
    VerificationResult,
    FinalResponse,
    ResearchSelection,
)
from backend.app.agent.tools import AgentToolHarness
from backend.app.models.provider import LLMError
from backend.app.mcp.client import MCPToolError
from backend.app.services.web import relevant_excerpt

LABELS = {
    "search_memory": "Memory searched",
    "search_remembered_facts": "Cross-case memory searched",
    "search_previous_tickets": "Previous incidents searched",
    "get_user_context": "User context checked",
    "get_company_context": "Company context checked",
    "get_site_context": "Site context checked",
    "get_device_context": "Device context checked",
    "get_active_alerts": "Active alerts checked",
    "get_recent_changes": "Recent changes checked",
    "search_knowledge": "Knowledge searched",
    "search_graph": "Relationships searched",
    "search_web": "Public sources searched",
    "fetch_web_page": "Public page fetched",
}


def now():
    return datetime.now(timezone.utc).isoformat()


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
            builder.add_node(name, getattr(self, name))
        builder.add_node("knowledge_subgraph", self.knowledge_subgraph)
        builder.add_node("case_subgraph", self.case_subgraph)
        builder.add_edge(START, "classify_intent")
        builder.add_edge("classify_intent", "understand_request")
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
            graph.add_node("understand_question", self.understand_question)
            graph.add_node("retrieval_context", self.retrieval_context)
            graph.add_node("formulate_retrieval_plan", self.plan)
            graph.add_edge(START, "understand_question")
            graph.add_edge("understand_question", "retrieval_context")
            graph.add_edge("retrieval_context", "formulate_retrieval_plan")
            graph.add_edge("formulate_retrieval_plan", "execute_tools")
        else:
            for name in (
                "analyze_ticket",
                "identify_entities",
                "load_memory",
                "formulate_investigation_plan",
            ):
                graph.add_node(
                    name,
                    self.plan if name == "formulate_investigation_plan" else getattr(self, name),
                )
            graph.add_edge(START, "analyze_ticket")
            graph.add_edge("analyze_ticket", "identify_entities")
            graph.add_edge("identify_entities", "load_memory")
            graph.add_edge("load_memory", "formulate_investigation_plan")
            graph.add_edge("formulate_investigation_plan", "execute_tools")
        graph.add_node("execute_tools", self.execute_tools)
        graph.add_node("observe", self.observe)
        graph.add_edge("execute_tools", "observe")
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
        result = await self.harness.invoke(name, arguments)
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

    async def run_request(self, request_id, summary=None, answer=None):
        lock = self._case_locks.setdefault(request_id, asyncio.Lock())
        async with lock:
            return await self._run_request(request_id, summary, answer)

    async def _run_request(self, request_id, summary=None, answer=None):
        """Classify before creating operational state; knowledge never creates a case."""
        if answer is None:
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
                "confirmed_facts": [],
                "remembered_facts": [],
                "human_answers": [],
                "contradictions": [],
                "research_iteration": 0,
                "research_rounds": 0,
            }
            self._request_events[request_id] = []
        else:
            snapshot = await self.graph.aget_state(self.config(request_id))
            if not snapshot.next or snapshot.values.get("status") != "needs_input":
                raise ValueError("This request is not awaiting human input")
            graph_input = Command(resume=answer)
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
                "analysis",
                "remembered_facts",
                "confirmed_facts",
                "human_answers",
                "contradictions",
                "decision",
                "verification",
                "decision_issues",
                "research_rounds",
                "available_context",
            )
        }
        result["evidence"] = evidence
        if include_tools:
            result["available_tools"] = self.harness.schemas(state.get("workflow", "service_desk"))
        return result

    async def classify_intent(self, state):
        intent = await self.provider.generate_structured(
            "classify_intent", {"request": state["ticket"]["summary"]}, RequestIntent
        )
        await self.note(
            state, "classify_intent", f"Request classified: {intent.workflow}", self.provider.name()
        )
        return {"intent": intent.model_dump(), "workflow": intent.workflow}

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
            "Knowledge workflow selected; evidence is required for an answer",
        )
        return {"analysis": {"symptoms": state["ticket"]["summary"], "entities": []}}

    async def retrieval_context(self, state):
        result = await self._call(state, "list_available_context", {})
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
        plan = await self.provider.generate_structured(
            "plan", self.context(state, True), InvestigationPlan
        )
        try:
            self.harness.validate_plan(plan, state["workflow"])
        except ValueError as exc:
            raise LLMError("Model plan failed tool permission or argument validation") from exc
        await self.note(
            state,
            "plan",
            f"Plan: {plan.goal}. " + "; ".join(c.name + ": " + c.reason for c in plan.tool_calls),
            self.provider.name(),
        )
        return {"plan": plan.model_dump(), "plan_executed": False}

    async def execute_tools(self, state):
        plan = InvestigationPlan.model_validate(state["plan"])
        try:
            self.harness.validate_plan(plan, state["workflow"])
        except ValueError as exc:
            raise LLMError("Model plan failed tool permission or argument validation") from exc
        results = [None] * len(plan.tool_calls)
        web_round = any(c.name == "search_web" for c in plan.tool_calls)

        async def execute(index):
            call = plan.tool_calls[index]
            try:
                if (
                    call.name in {"search_web", "fetch_web_page"}
                    and state.get("research_rounds", 0) >= self.settings.max_research_rounds
                ):
                    raise MCPToolError("Public research round limit reached")
                results[index] = {
                    "name": call.name,
                    "output": await self._call(state, call.name, call.arguments),
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
        return {
            "tool_results": results,
            "plan_executed": True,
            "research_rounds": state.get("research_rounds", 0) + int(web_round),
        }

    def _evidence(self, state, name, result):
        if not isinstance(result, dict) or "items" not in result:
            rows = result if isinstance(result, list) else [result] if result else []
            result = {
                "items": rows,
                "timestamp": now(),
                "source_system": "public_web"
                if name in {"search_web", "fetch_web_page"}
                else "capability",
                "reliability": 0.6,
            }
        rows = result["items"]
        if result.get("status") == "error":
            rows = [{"error": result.get("error")}]
        items = []
        for row in rows:
            meta = row.get("metadata", {})
            system = row.get("source_system") or result["source_system"]
            stamp = row.get("observed_at") or meta.get("created_at") or result["timestamp"]
            kind = (
                "tool_error"
                if system == "tool_error"
                else "confirmed_fact"
                if name in {"search_memory", "remember_fact"}
                else "public_documentation"
                if system == "public_web" or meta.get("source_type") == "web"
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
                    source=row.get("source") or meta.get("source") or row.get("url") or name,
                    content=row,
                    entities=state.get("analysis", {}).get("entities", []),
                    reliability=result["reliability"],
                    relevance=1 / (1 + max(float(row.get("score", 0)), 0)),
                    case_id=state["case_id"] if state.get("persist_case") is not False else None,
                    url=row.get("url") or meta.get("url") or None,
                    doc_id=meta.get("doc_id"),
                ).model_dump()
            )
        return items

    async def observe(self, state):
        evidence = list(state.get("evidence", []))
        seen = {e["id"] for e in evidence}
        for item in state.get("tool_results", []):
            for row in self._evidence(state, item["name"], item["output"]):
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
        return {"evidence": evidence, "contradictions": contradictions, "tool_results": []}

    async def reason(self, state):
        iteration = state.get("research_iteration", 0) + 1
        context = self.context(state, True)
        if state.get("decision_issues"):
            # An erroneous critic can repeat the invalid premise it was asked
            # to judge. Application validation takes priority during revision.
            context.pop("verification", None)
        decision = await self.provider.generate_structured("reason", context, InvestigationDecision)
        self._validate_evidence_refs(
            state, [i for h in decision.hypotheses for i in h.evidence_ids]
        )
        issues = []
        if state["workflow"] == "knowledge" and decision.human_field in {
            "current_site",
            "required_update_installed",
        }:
            issues.append("A ticket-specific question is not permitted for this knowledge request.")
        if decision.human_field == "current_site":
            product = (state.get("analysis", {}).get("product") or "").casefold()
            import re

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
        if not self.usable_evidence(state):
            decision.enough_information = False
            decision.confidence = 0
        await self.note(
            state,
            "reason",
            decision.decision_justification or decision.evidence_summary,
            self.provider.name(),
        )
        return {
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

    @staticmethod
    def usable_evidence(state):
        return [
            e
            for e in state.get("evidence", [])
            if e["source_type"] != "tool_error"
            and e["tool"] not in {"list_available_context", "search_web", "user_request"}
            and not e["content"].get("available") is False
        ]

    def route(self, state):
        if state["research_iteration"] >= self.settings.max_investigation_cycles:
            return "finalize"
        d = state["decision"]
        if d.get("requested_tools"):
            return "plan"
        if (
            d.get("needs_web_research")
            and state.get("research_rounds", 0) < self.settings.max_research_rounds
        ):
            return "research"
        return "verify"

    async def ask_human(self, state):
        # The request event has a stable key, so interrupt replay cannot duplicate it.
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
        }

    async def research(self, state):
        decision = state["decision"]
        queries = decision.get("research_queries", [])
        if not queries:
            raise ValueError(
                "Public research requires a model-formulated query tied to an evidence gap"
            )
        outputs = []
        domains = decision.get("preferred_domains", [])
        from urllib.parse import urlparse

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
            try:
                search = await self._call(state, "search_web", {"query": query, "max_results": 3})
            except MCPToolError as exc:
                await failed("search_web", exc)
                continue
            outputs.append({"name": "search_web", "output": search})
            rows = (
                search.get("items", [])
                if isinstance(search, dict) and "items" in search
                else search
            )
            rows = sorted(
                rows,
                key=lambda r: 0
                if any(
                    (urlparse(r.get("url", "")).hostname or "").casefold() == d.casefold()
                    or (urlparse(r.get("url", "")).hostname or "")
                    .casefold()
                    .endswith("." + d.casefold())
                    for d in domains
                )
                else 1,
            )
            selection = await self.provider.generate_structured(
                "evaluate_sources",
                {
                    "ticket": state["ticket"],
                    "request": state["ticket"]["summary"],
                    "query": query,
                    "results": rows,
                    "preferred_domains": domains,
                    "missing_evidence": decision.get("missing_information", []),
                },
                ResearchSelection,
            )
            available = {r["url"]: r for r in rows}
            if set(selection.selected_urls) - set(available):
                raise LLMError("Research selection contains a URL absent from search results")
            await self.note(
                state, "evaluate_sources", selection.justification, self.provider.name()
            )
            for url in selection.selected_urls:
                hit = available[url]
                capability_name = "fetch_web_page"
                try:
                    fetched = await self._call(state, "fetch_web_page", {"url": hit["url"]})
                    outputs.append({"name": "fetch_web_page", "output": fetched})
                    page = fetched["result"] if "result" in fetched else fetched
                    text = page.get("text", "")
                    if len(text.strip()) < 120:
                        continue
                    useful = await self.provider.generate_structured(
                        "evaluate_sources",
                        {
                            "request": state["ticket"]["summary"],
                            "query": query,
                            "results": [
                                {
                                    "url": page["url"],
                                    "title": page.get("title"),
                                    "text": relevant_excerpt(text, query, 6000),
                                }
                            ],
                            "preferred_domains": domains,
                            "missing_evidence": decision.get("missing_information", []),
                            "purpose": "Assess actual fetched content for usefulness before ingestion.",
                        },
                        ResearchSelection,
                    )
                    if set(useful.selected_urls) - {page["url"]}:
                        raise LLMError("Fetched-source evaluation selected an unknown URL")
                    await self.note(
                        state, "evaluate_sources", useful.justification, self.provider.name()
                    )
                    if not useful.selected_urls:
                        continue
                    # Public material stays untrusted, with URL/document provenance.
                    capability_name = "ingest_text"
                    await self._call(
                        state,
                        "ingest_text",
                        {
                            "text": text,
                            "title": page.get("title"),
                            "source": page["url"],
                            "source_type": "web",
                            "url": page["url"],
                        },
                    )
                except MCPToolError as exc:
                    await failed(capability_name, exc)
        knowledge = await self._call(
            state,
            "search_knowledge",
            {"question": state["ticket"]["summary"], "mode": "HYBRID", "top_k": 6},
        )
        outputs.append({"name": "search_knowledge", "output": knowledge})
        return {"tool_results": outputs, "research_rounds": state.get("research_rounds", 0) + 1}

    def _validate_evidence_refs(self, state, ids):
        if set(ids) - {e["id"] for e in state.get("evidence", [])}:
            raise LLMError("Structured reasoning cited unknown evidence identifiers")

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
            )
        }
        verification = await self.provider.generate_structured(
            "verify", context, VerificationResult
        )
        self._validate_evidence_refs(state, verification.evidence_ids)
        if state.get("decision_issues"):
            verification.unsupported_claims = list(
                dict.fromkeys(verification.unsupported_claims + state["decision_issues"])
            )
            verification.should_continue_research = True
        if not self.usable_evidence(state) and not state["decision"].get("question_for_human"):
            verification.confidence = 0
            verification.unsupported_claims.append("No supporting evidence is available.")
        await self.note(state, "verify", verification.justification, self.provider.name())
        return {
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

    def verification_route(self, state):
        v = state["verification"]
        decision = state["decision"]
        if state["research_iteration"] >= self.settings.max_investigation_cycles:
            return "finalize"
        if v["contradictions"] and v["should_continue_research"]:
            return "reason"
        if decision.get("question_for_human"):
            if not v["unsupported_claims"] and not v["contradictions"]:
                return "ask_human"
            return "reason" if v["should_continue_research"] else "finalize"
        if v["should_continue_research"]:
            return "plan"
        return "remember" if not v["unsupported_claims"] else "finalize"

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
        d = InvestigationDecision.model_validate(state["decision"])
        v = state.get("verification", {})
        limited = state["research_iteration"] >= self.settings.max_investigation_cycles
        supported = (
            bool(v)
            and not v.get("unsupported_claims")
            and d.enough_information
            and bool(self.usable_evidence(state))
            and not limited
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
        response = FinalResponse(
            issue_summary=state["ticket"]["summary"],
            answer=d.answer if supported else None,
            likely_causes=d.hypotheses,
            confirmed_facts=state.get("confirmed_facts", []),
            inferences=d.inferences,
            contradictions=state.get("contradictions", []),
            evidence=state.get("evidence", []),
            actions_already_taken=[e["content"] for e in events if e.get("tool") != "error"],
            recommended_next_action=d.recommended_next_action
            if not v.get("unsupported_claims")
            else "Collect missing evidence before accepting the candidate assessment.",
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
        return {
            "status": "assessment_ready" if supported else "insufficient_information",
            "confidence": confidence,
            "pending_question": None,
            "pending_field": None,
            "uncertainty": uncertainty,
            "final_response": response.model_dump(),
            "final_answer": d.answer or d.recommended_next_action if supported else None,
            "likely_issue": d.likely_issue if not v.get("unsupported_claims") else None,
            "recommended_next_action": response.recommended_next_action,
            "actions_already_taken": response.actions_already_taken,
        }
