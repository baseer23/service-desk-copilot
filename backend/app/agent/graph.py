"""The sole case orchestrator: durable LangGraph, structured reasoning, MCP tools."""

from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from backend.app.agent.models import TicketAnalysis, InvestigationDecision, MemoryFact
from backend.app.agent.state import InvestigationState
from backend.app.agent.tools import AgentToolHarness, READ_TOOLS
from backend.app.repositories.fixtures import canonical_site

LABELS = {
    "search_knowledge": "Knowledge searched",
    "search_previous_tickets": "Previous incidents searched",
    "get_user_context": "User context checked",
    "get_company_context": "Company context checked",
    "get_site_context": "Site context checked",
    "get_device_context": "Device context checked",
    "get_active_alerts": "Alerts checked",
    "get_recent_changes": "Recent changes checked",
    "search_remembered_facts": "Cross-case memory searched",
    "remember_fact": "Confirmed fact saved",
    "search_web": "Public web searched",
    "fetch_web_page": "Public page fetched",
    "save_case_note": "Case note saved",
}


class ServiceDeskAgent:
    def __init__(self, settings, mcp_client, memory_store, provider, checkpointer, ingest_service):
        self.settings, self.memory, self.provider, self.ingest = (
            settings,
            memory_store,
            provider,
            ingest_service,
        )
        self.harness = AgentToolHarness(mcp_client)
        self._case_locks = {}
        graph = StateGraph(InvestigationState)
        for name in (
            "analyze_ticket",
            "load_memory",
            "investigate",
            "reason",
            "execute_tools",
            "ask_human",
            "research_web",
            "finalize",
        ):
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, "analyze_ticket")
        graph.add_edge("analyze_ticket", "load_memory")
        graph.add_edge("load_memory", "investigate")
        graph.add_edge("investigate", "reason")
        graph.add_conditional_edges("reason", self.route)
        graph.add_edge("execute_tools", "investigate")
        graph.add_edge("ask_human", "investigate")
        graph.add_edge("research_web", "reason")
        graph.add_edge("finalize", END)
        self.graph = graph.compile(checkpointer=checkpointer)

    def config(self, case_id):
        return {"configurable": {"thread_id": case_id}, "recursion_limit": 100}

    async def run_case(self, case_id, answer=None):
        lock = self._case_locks.setdefault(case_id, asyncio.Lock())
        async with lock:
            stored = self.memory.load_case(case_id)
            if stored is None:
                raise KeyError(case_id)
            config = self.config(case_id)
            checkpoint = await self.graph.aget_state(config)
            if answer is not None:
                if not checkpoint.next or stored.get("status") != "needs_input":
                    raise ValueError("This case is not awaiting human input")
                graph_input = Command(resume=answer)
            elif checkpoint.values:
                # Retry a failed node using its checkpoint, never START. Paused
                # human interrupts still require Command(resume=answer).
                if stored.get("status") == "error" and checkpoint.next:
                    graph_input = None
                else:
                    return self._save_snapshot(case_id, checkpoint.values)
            else:
                graph_input = {**stored, "status": "investigating", "research_iteration": 0}
            try:
                await self.graph.ainvoke(graph_input, config)
                snapshot = await self.graph.aget_state(config)
                return self._save_snapshot(case_id, snapshot.values)
            except Exception:
                snapshot = await self.graph.aget_state(config)
                state = dict(snapshot.values or stored)
                state.update(
                    status="error",
                    uncertainty=["Investigation failed; inspect the explicit API error."],
                )
                self.memory.save_case(case_id, state)
                self.memory.record_event(case_id, "error", "application", "Investigation failed")
                raise

    def _save_snapshot(self, case_id, values):
        state = dict(values)
        self.memory.save_case(case_id, state)
        return state

    async def _call(self, state, name, arguments):
        result = await self.harness.invoke(name, arguments)
        count = len(result) if isinstance(result, list) else int(bool(result))
        rows = result if isinstance(result, list) else [result] if result else []
        source = rows[0].get("source_system") if rows else None
        source = source or ("chroma" if name == "search_knowledge" else "capability")
        self.memory.record_event(
            state["case_id"],
            name,
            source,
            f"{LABELS.get(name, name)}: {count} result(s)",
        )
        return result

    @staticmethod
    def _evidence(name, result):
        rows = result if isinstance(result, list) else [result] if result else []
        evidence = []
        for row in rows:
            meta = row.get("metadata", {})
            evidence.append(
                {
                    "tool": name,
                    "source": row.get("source") or meta.get("source") or row.get("url") or name,
                    "source_system": row.get("source_system")
                    or ("chroma" if name == "search_knowledge" else "sqlite_memory"),
                    "observed_at": row.get("observed_at")
                    or meta.get("created_at")
                    or datetime.now(timezone.utc).isoformat(),
                    "content": row,
                }
            )
        return evidence

    async def analyze_ticket(self, state):
        analysis = await self.provider.generate_structured(
            "analyze_ticket", {"ticket": state["ticket"]}, TicketAnalysis
        )
        self.memory.record_event(
            state["case_id"], "analyze_ticket", self.provider.name(), "Ticket analyzed"
        )
        return {
            "analysis": analysis.model_dump(),
            "hypotheses": analysis.initial_hypotheses,
            "evidence": [],
            "confirmed_facts": [],
            "human_answers": [],
            "tool_events": [],
            "web_sources": [],
        }

    async def load_memory(self, state):
        subject = state["analysis"].get("user_name")
        facts = (
            await self._call(state, "search_remembered_facts", {"subject": subject})
            if subject
            else []
        )
        # Latest confirmed value per predicate; retain provenance and original case ID.
        useful = {}
        for fact in facts:
            if fact.get("reliability") == "confirmed" and fact.get("source") in {
                "human_confirmed",
                "trusted_tool",
                "resolved_outcome",
            }:
                useful.setdefault(fact["predicate"], fact)
        return {"remembered_facts": list(useful.values())}

    def _fact_values(self, state):
        values = {f["predicate"]: f["value"] for f in state.get("remembered_facts", [])}
        values.update({f["predicate"]: f["value"] for f in state.get("confirmed_facts", [])})
        return values

    async def investigate(self, state):
        a = state["analysis"]
        site = self._fact_values(state).get("current_site") or a.get("site")
        if "previous_tickets" in state and state.get("context_site") == site:
            return {}
        calls = [
            (
                "search_knowledge",
                {
                    "question": state["ticket"]["summary"],
                    "mode": "HYBRID",
                    "top_k": self.settings.top_k,
                },
            ),
            (
                "search_previous_tickets",
                {
                    "query": a["symptoms"],
                    "user": a.get("user_name"),
                    "company": a.get("company"),
                    "site": site,
                },
            ),
        ]
        if a.get("user_name"):
            calls += [
                ("get_user_context", {"user_name": a["user_name"], "company": a.get("company")}),
                ("get_device_context", {"device_or_user": a.get("device") or a["user_name"]}),
            ]
        if a.get("company"):
            calls += [
                ("get_company_context", {"company": a["company"]}),
                (
                    "get_active_alerts",
                    {"company": a["company"], "site": site, "product": a.get("product")},
                ),
                (
                    "get_recent_changes",
                    {"company": a["company"], "site": site, "product": a.get("product")},
                ),
            ]
            if site:
                calls += [("get_site_context", {"company": a["company"], "site": site})]
        evidence = [e for e in state.get("evidence", []) if e["tool"] not in {n for n, _ in calls}]
        update = {
            "context_site": site,
            "previous_tickets": [],
            "knowledge": [],
            "alerts": [],
            "changes": [],
        }
        destinations = {
            "search_knowledge": "knowledge",
            "search_previous_tickets": "previous_tickets",
            "get_active_alerts": "alerts",
            "get_recent_changes": "changes",
        }
        for name, arguments in calls:
            result = await self._call(state, name, arguments)
            evidence += self._evidence(name, result)
            if name in destinations:
                update[destinations[name]] = result
        update["evidence"] = evidence
        return update

    async def reason(self, state):
        iteration = state.get("research_iteration", 0) + 1
        context = {
            k: state.get(k, [])
            for k in (
                "confirmed_facts",
                "remembered_facts",
                "evidence",
                "previous_tickets",
                "knowledge",
                "alerts",
                "changes",
                "human_answers",
                "web_sources",
            )
        }
        context.update(
            analysis=state["analysis"],
            available_tools=self.harness.schemas(),
            web_attempted=bool(state.get("web_sources"))
            or any(e["tool"] == "search_web" for e in self.memory.list_events(state["case_id"])),
        )
        decision = await self.provider.generate_structured("reason", context, InvestigationDecision)
        if not state.get("evidence") and decision.enough_information:
            decision.enough_information = False
            decision.confidence = 0
            decision.uncertainty.append("No supporting evidence is available.")
        if iteration >= self.settings.max_investigation_cycles:
            decision.question_for_human = None
            decision.human_field = None
            decision.requested_tools = []
            decision.needs_web_research = False
            decision.enough_information = False
            decision.uncertainty.append(
                "Investigation cycle limit reached; additional technician evidence is required."
            )
        self.memory.record_event(
            state["case_id"],
            "reason",
            self.provider.name(),
            decision.decision_justification or decision.evidence_summary,
        )
        return {
            "decision": decision.model_dump(),
            "research_iteration": iteration,
            "hypotheses": decision.hypotheses,
            "inferences": decision.inferences,
            "missing_information": decision.missing_information,
            "confidence": decision.confidence,
            "uncertainty": decision.uncertainty,
            "likely_issue": decision.likely_issue,
            "recommended_next_action": decision.recommended_next_action,
            "pending_question": decision.question_for_human,
            "pending_field": decision.human_field,
            "requested_tools": [t.model_dump() for t in decision.requested_tools],
            "status": "needs_input" if decision.question_for_human else "investigating",
        }

    @staticmethod
    def route(state):
        d = state["decision"]
        if d.get("question_for_human"):
            return "ask_human"
        if d.get("requested_tools"):
            return "execute_tools"
        if d.get("needs_web_research"):
            return "research_web"
        return "finalize"

    async def execute_tools(self, state):
        evidence = list(state.get("evidence", []))
        update = {}
        destinations = {
            "search_knowledge": "knowledge",
            "search_previous_tickets": "previous_tickets",
            "get_active_alerts": "alerts",
            "get_recent_changes": "changes",
        }
        for request in state["requested_tools"]:
            if request["name"] not in READ_TOOLS:
                raise ValueError("Reasoning may only request read capabilities")
            result = await self._call(state, request["name"], request["arguments"])
            evidence += self._evidence(request["name"], result)
            if request["name"] in destinations:
                update[destinations[request["name"]]] = result
        return {**update, "evidence": evidence}

    async def ask_human(self, state):
        answer = interrupt(
            {
                "question": state["pending_question"],
                "field": state["pending_field"],
                "case_id": state["case_id"],
            }
        )
        answer = str(answer).strip()
        if not answer:
            raise ValueError("Human answer cannot be empty")
        a, field = state["analysis"], state["pending_field"]
        value = (
            canonical_site(answer, a.get("company") or "") if field == "current_site" else answer
        )
        if field == "required_update_installed":
            lower = answer.lower()
            if lower in {"yes", "installed", "yes installed", "yes, installed"}:
                value = "Yes"
            elif lower in {"no", "not installed"}:
                value = "No"
        fact = MemoryFact(
            subject=a.get("user_name") or state["case_id"],
            predicate=field or "human_answer",
            value=value,
            source="human_confirmed",
            source_system="human_input",
            observed_at=datetime.now(timezone.utc).isoformat(),
            confidence=1,
            case_id=state["case_id"],
            metadata={"question": state["pending_question"], "answer": answer},
        )
        saved = await self._call(state, "remember_fact", {"fact": fact.model_dump()})
        await self._call(
            state,
            "save_case_note",
            {
                "case_id": state["case_id"],
                "text": f"Human confirmed {field}: {value}",
                "source": "human_confirmed",
            },
        )
        return {
            "confirmed_facts": state.get("confirmed_facts", []) + [saved],
            "human_answers": state.get("human_answers", [])
            + [{"question": state["pending_question"], "answer": answer}],
            "pending_question": None,
            "pending_field": None,
            "status": "investigating",
        }

    async def research_web(self, state):
        evidence, sources = list(state.get("evidence", [])), list(state.get("web_sources", []))
        try:
            results = await self._call(
                state, "search_web", {"query": state["ticket"]["summary"], "max_results": 3}
            )
            for result in results[:2]:
                try:
                    page = await self._call(state, "fetch_web_page", {"url": result["url"]})
                    if not page.get("text") or len(page["text"].strip()) < 80:
                        continue
                    # Canonical IngestService is reused; repositories stay out of LangChain tools.
                    await asyncio.to_thread(
                        self.ingest.ingest_text,
                        page["title"],
                        page["text"],
                        source=page["url"],
                        source_type="web",
                        url=page["url"],
                    )
                    sources.append(page)
                    evidence += self._evidence("fetch_web_page", page)
                    self.memory.record_event(
                        state["case_id"],
                        "ingest_web",
                        "ingestion",
                        f"Web knowledge ingested: {page['title']}",
                    )
                except Exception:
                    self.memory.record_event(
                        state["case_id"],
                        "fetch_web_page",
                        "public_web",
                        "Web page could not be safely fetched or ingested",
                    )
            knowledge = await self._call(
                state,
                "search_knowledge",
                {
                    "question": state["ticket"]["summary"],
                    "mode": "HYBRID",
                    "top_k": self.settings.top_k,
                },
            )
            evidence += self._evidence("search_knowledge", knowledge)
        except Exception:
            knowledge = state.get("knowledge", [])
            self.memory.record_event(
                state["case_id"], "search_web", "public_web", "Public web research unavailable"
            )
        return {"web_sources": sources, "knowledge": knowledge, "evidence": evidence}

    async def finalize(self, state):
        enough = state["decision"]["enough_information"] and bool(state.get("evidence"))
        status = "assessment_ready" if enough else "insufficient_information"
        action = (
            state.get("recommended_next_action")
            or "Collect diagnostic information with the service desk technician."
        )
        uncertainty = state.get("uncertainty", [])
        if not enough and not uncertainty:
            uncertainty = ["The available evidence is insufficient to recommend a cause."]
        self.memory.record_event(
            state["case_id"],
            "finalize",
            "agent",
            "Assessment ready" if enough else "Insufficient information",
        )
        return {
            "status": status,
            "pending_question": None,
            "pending_field": None,
            "final_answer": action,
            "uncertainty": uncertainty,
            "confidence": state.get("confidence", 0) if state.get("evidence") else 0,
        }
