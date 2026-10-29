"""Run the three approaches on the same documents and recorded model output."""

from __future__ import annotations

import json
from types import SimpleNamespace

from backend.app.experiments.probabilistic_kg.evidence import EvidenceIndex
from backend.app.experiments.probabilistic_kg.fixtures import DOCUMENTS, INGESTION_ORDER
from backend.app.experiments.probabilistic_kg.models import ValidatedGraph
from backend.app.experiments.probabilistic_kg.mutations import compile_plan, entity_id, scope
from backend.app.experiments.probabilistic_kg.reasoner import (
    STAGE,
    ProbabilisticGraphReasoner,
    RecordedProvider,
    existing_entity_summaries,
)
from backend.app.experiments.probabilistic_kg.recorded import (
    BaselineRecordedProvider,
    proposal_a,
    proposal_b,
)
from backend.app.experiments.probabilistic_kg.retrieval import Fact, GraphView
from backend.app.experiments.probabilistic_kg.validation import ProposalValidator
from backend.app.services.document_processing import with_heading_sections
from backend.app.store.graph_repo import InMemoryGraphRepository

TENANT = "experiment-tenant"
BASELINE_SETTINGS = SimpleNamespace(
    chunk_tokens=400, semantic_graph_batch_chars=12000, semantic_graph_max_llm_calls_per_job=30,
    semantic_graph_max_entities_per_document=100,
    semantic_graph_max_relationships_per_document=200,
)


def build_index():
    index = EvidenceIndex()
    for document_id in INGESTION_ORDER:
        title, extraction = DOCUMENTS[document_id]
        index.add_document(document_id, title, extraction)
    return index


# --- Approach A: dev's existing semantic pipeline --------------------------------


class MemoryRecords:
    """The read/write contract DocumentGraphUnderstanding uses, in memory."""

    def __init__(self):
        self.rows = {}

    def read(self, kind, key):
        return self.rows.get((kind, key))

    def write(self, kind, key, payload, previous=None, create_only=False, fields=None):
        row = {"payload": json.loads(json.dumps(payload))}
        self.rows[(kind, key)] = row
        return row


class EvalGraphRepository(InMemoryGraphRepository):
    """In-memory graph whose candidate discovery mirrors the Neo4j query.

    GraphRepository.find_entity_candidates matches name, aliases, or an
    identifier value contained in the entity's identifiers; the base
    in-memory class matches names only, which would understate A.
    """

    def find_entity_candidates(self, name, entity_type=None, aliases=None, identifiers=None,
                               limit=20):
        terms = {value.casefold() for value in [name, *(aliases or [])] if value.strip()}
        wanted = [str(v).casefold() for v in (identifiers or {}).values() if str(v).strip()]
        rows = []
        for key, value in self.entity_links.items():
            names = {value["name"].casefold(), *(a.casefold() for a in value.get("aliases", []))}
            serialized = json.dumps(value.get("identifiers", {}), sort_keys=True).casefold()
            if names & terms or any(item in serialized for item in wanted):
                rows.append({"id": key, "name": value["name"], "kind": value.get("kind")})
        return rows[:limit]


class _EvidenceVectorStore:
    """resolve_object_ids over the evidence index (dev hydrates from Chroma)."""

    def __init__(self, index):
        self.index = index

    def resolve_object_ids(self, object_ids, query_embedding=None):
        return [{"id": value, "metadata": {"object_id": value}}
                for value in object_ids if self.index.get(value) is not None]


async def run_baseline(index):
    from backend.app.services.document_graph_understanding import DocumentGraphUnderstanding
    from backend.app.services.graph_discovery import GraphDiscovery
    from backend.app.services.graph_placement import GraphPlacementService

    graph, records = EvalGraphRepository(), MemoryRecords()
    provider = BaselineRecordedProvider(index)
    vectors = _EvidenceVectorStore(index)
    results, not_representable, partial = {}, [], []
    for document_id in INGESTION_ORDER:
        title, extraction = DOCUMENTS[document_id]
        understanding = DocumentGraphUnderstanding(BASELINE_SETTINGS, provider, records)
        proposal, is_partial = await understanding.understand_document(
            document_id, with_heading_sections(extraction), {"version": 1, "title": title}
        )
        if is_partial:
            partial.append(document_id)
            continue
        not_representable.extend(proposal_a(index, document_id)[1])
        discovery = GraphDiscovery(graph).discover(proposal)
        placement = GraphPlacementService(provider, graph, vectors, records)
        plan = await placement.prepare_placement(proposal, discovery=discovery)
        results[document_id] = placement.apply_placement(plan, proposal)
        graph.update_document_semantics(document_id, document_type=proposal.document_type,
                                        summary=proposal.summary, status="linked")
    view = GraphView("A")
    for key, value in graph.entity_links.items():
        view.entities[key] = {"name": value["name"], "type": value.get("kind"),
                              "aliases": value.get("aliases", [])}
    seen = set()
    for row in graph.knowledge_relationships:
        identity = (row["source_id"], row["predicate"], row["target_id"], row["document_id"],
                    tuple(row.get("evidence_object_ids", [])))
        if identity in seen:  # GraphRepository MERGEs claims by this identity
            continue
        seen.add(identity)
        view.facts.append(Fact(
            id=str(len(view.facts)), subject=row["source_id"], predicate=row["predicate"],
            object=row["target_id"], document_id=row["document_id"],
            evidence_ids=list(row.get("evidence_object_ids", [])),
        ))
    return view, {
        "model_calls": len(provider.calls), "placement": results,
        "not_representable": not_representable, "partial_documents": partial,
        "mentions": len(graph.document_mentions),
    }


# --- Approaches B and C: probabilistic reasoning + validation -----------------------


async def reason_and_validate(index, provider=None):
    """One pass shared by B and C: propose per document, validate, compile."""
    if provider is None:
        provider = RecordedProvider({
            (STAGE, document_id): proposal_b(index, document_id).model_dump()
            for document_id in INGESTION_ORDER
        })
    reasoner, validator = ProbabilisticGraphReasoner(provider), ProposalValidator(index)
    graph = ValidatedGraph()
    proposals = []
    for document_id in INGESTION_ORDER:
        proposal = await reasoner.propose(index, document_id, existing_entity_summaries(graph))
        proposals.append(proposal)
        graph = validator.validate([proposal], existing=graph)
    plan = compile_plan(graph, index, TENANT)
    return graph, plan, proposals, getattr(provider, "calls", [])


def view_from_export(approach, nodes, edges, tenant=TENANT):
    """Rebuild a GraphView from what a transport actually wrote (read back)."""
    prefix = scope(tenant, "")
    by_id = {row["id"]: row for row in nodes}
    view = GraphView(approach)
    for row in nodes:
        if "Entity" in row["labels"]:
            props = row["props"]
            view.entities[row["id"]] = {"name": props.get("name"), "type": props.get("kind"),
                                        "aliases": list(props.get("aliases") or [])}
    links = {}
    for edge in edges:
        links.setdefault(edge["source"], []).append(edge)

    def target(node_id, relationship):
        return next((e["target"] for e in links.get(node_id, []) if e["type"] == relationship), None)

    for row in nodes:
        labels, props = row["labels"], row["props"]
        if "GraphClaim" in labels or "GraphHypothesis" in labels:
            evidence = [e["target"][len(prefix):] for e in links.get(row["id"], [])
                        if e["type"] == "SUPPORTED_BY"]
            document = target(row["id"], "DOCUMENTED_IN") or target(row["id"], "PROPOSED_IN")
            fact = Fact(
                id=props.get("original_id", row["id"]), subject=target(row["id"], "SUBJECT"),
                predicate=props.get("predicate"), object=target(row["id"], "OBJECT"),
                document_id=(document or "")[len(prefix):], evidence_ids=evidence,
                status=props.get("status", "hypothesis"), contested=bool(props.get("contested")),
            )
            (view.facts if "GraphClaim" in labels else view.hypotheses).append(fact)
        elif "Contradiction" in labels:
            view.contradictions.append({
                "id": props.get("original_id"),
                "evidence_ids": [e["target"][len(prefix):] for e in links.get(row["id"], [])
                                 if e["type"] == "SUPPORTED_BY"],
                "claims": [by_id.get(e["target"], {}).get("props", {}).get("original_id")
                           for e in links.get(row["id"], []) if e["type"] == "INVOLVES"],
            })
    return view


def expected_entity_ids(graph, tenant=TENANT):
    return {scope(tenant, entity_id(key)): key for key in graph.entities}
