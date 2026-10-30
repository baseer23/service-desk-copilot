"""Deterministic and agentic retrieval over any approach's resulting graph.

Both strategies read the same ``GraphView`` (entities, verified facts,
hypotheses, contradictions) plus the shared evidence index, so differences
in answers come from the graph each approach built.

Routing: an exact form or table question is answered by indexed lookup and
never reaches agentic planning. Otherwise the deterministic strategy does a
one-hop lookup (dev-like), and the agentic strategy follows the planner's
per-hop predicate choices with a visited set, a fan-out cap and a hop limit.
Hypotheses are never traversed; contested facts are reported as a conflict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from backend.app.experiments.probabilistic_kg.validation import mentions, normalize

MAX_HOPS = 3
MAX_FANOUT = 5
FORM_MATCH = 85
HEADER_MATCH = 85


@dataclass
class Fact:
    id: str
    subject: str
    predicate: str
    object: str
    document_id: str
    evidence_ids: list[str]
    status: str = "documented"
    contested: bool = False


@dataclass
class GraphView:
    approach: str
    entities: dict[str, dict] = field(default_factory=dict)  # id -> name, type, aliases
    facts: list[Fact] = field(default_factory=list)
    hypotheses: list[Fact] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)

    def names(self, entity_id):
        entity = self.entities.get(entity_id, {})
        return [entity.get("name", ""), *entity.get("aliases", [])]


@dataclass
class Answer:
    strategy: str
    answer: str | None
    evidence_ids: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    graph_operations: int = 0
    hydrated: int = 0
    model_calls: int = 0
    visited: int = 0


def _words(text):
    return {word[:5] for word in re.findall(r"[a-z0-9]+", normalize(text)) if len(word) > 2}


class Retriever:
    def __init__(self, view: GraphView, index):
        self.view, self.index = view, index

    # -- deterministic exact lookups ------------------------------------------
    def exact(self, question):
        best = None
        for record in self.index.form_fields():
            score = fuzz.token_set_ratio(normalize(question), normalize(record.extra["question"]))
            if score >= FORM_MATCH and (best is None or score > best[0]):
                best = (score, record)
        if best:
            record = best[1]
            value = (record.extra["selected_option"]
                     if record.extra.get("field_status") == "answered" else "AMBIGUOUS")
            return Answer("exact_form", value, [record.evidence_id], graph_operations=1,
                          hydrated=1)
        candidates = []
        for record in self.index.records.values():
            if record.label != "TableCell" or record.extra.get("kind") != "content":
                continue
            row_ok = any(
                fuzz.partial_ratio(normalize(header.split(" / ")[-1]), normalize(question))
                >= HEADER_MATCH for header in record.extra.get("row_headers", []) if header
            )
            column_ok = any(
                fuzz.partial_ratio(normalize(header.split(" / ")[-1]), normalize(question))
                >= HEADER_MATCH for header in record.extra.get("column_headers", []) if header
            )
            if row_ok and column_ok:
                candidates.append(record)
        if len(candidates) == 1:
            record = candidates[0]
            return Answer("exact_table", record.text, [record.evidence_id], graph_operations=1,
                          hydrated=1)
        return None

    # -- graph strategies --------------------------------------------------------
    def _seeds(self, names):
        seeds = set()
        for entity_id in self.view.entities:
            if any(mentions(name, self.view.names(entity_id)) for name in names) or any(
                normalize(name) in {normalize(n) for n in self.view.names(entity_id)}
                for name in names
            ):
                seeds.add(entity_id)
        return seeds

    def _finish(self, strategy, facts, operations, model_calls, visited, boolean=False):
        evidence = list(dict.fromkeys(e for fact in facts for e in fact.evidence_ids))
        hydrated = sum(1 for value in evidence if self.index.get(value) is not None)
        if not facts:
            return Answer(strategy, "NOT_ESTABLISHED", graph_operations=operations,
                          model_calls=model_calls, visited=visited)
        if boolean:
            return Answer(strategy, "YES", evidence, graph_operations=operations,
                          hydrated=hydrated, model_calls=model_calls, visited=visited)
        values = sorted({self.view.entities.get(f.object, {}).get("name", f.object) for f in facts})
        contested = any(fact.contested for fact in facts)
        if len(values) > 1 and contested:
            return Answer(strategy, "CONFLICT", evidence, values, operations, hydrated,
                          model_calls, visited)
        return Answer(strategy, values[0], evidence, values, operations, hydrated, model_calls,
                      visited)

    def graph_first(self, question):
        """One hop from entities named in the question; predicate chosen by word overlap."""
        seeds = self._seeds([question])
        operations = 1
        touching = [fact for fact in self.view.facts if fact.subject in seeds]
        operations += 1
        words = _words(question)
        scored = []
        for fact in touching:
            overlap = len(words & _words(fact.predicate.replace("_", " ")))
            if overlap:
                scored.append((overlap, fact))
        if not scored:
            return self._finish("graph_first", [], operations, 0, len(seeds))
        top = max(score for score, _ in scored)
        chosen = [fact for score, fact in scored if score == top]
        boolean = question.strip().casefold().startswith(("is ", "does ", "are "))
        return self._finish("graph_first", chosen, operations, 0, len(seeds), boolean)

    def agentic(self, decision):
        """Follow the planner's predicate choices hop by hop, within fixed bounds."""
        model_calls = 1  # interpreting the question into seeds and hop choices
        frontier = self._seeds(decision["seeds"])
        visited = set(frontier)
        operations = 1
        traversed = []
        for hop in decision["hops"][:MAX_HOPS]:
            model_calls += 1  # choosing which relationships to follow
            wanted = set(hop)
            step = [fact for fact in self.view.facts
                    if fact.subject in frontier and fact.predicate in wanted][:MAX_FANOUT]
            operations += 1
            if not step:
                traversed = []
                break
            traversed = step
            frontier = {fact.object for fact in step} - visited
            visited |= frontier
            if not frontier and not decision.get("boolean"):
                break
        model_calls += 1  # writing the grounded answer
        return self._finish("agentic", traversed, operations, model_calls, len(visited),
                            decision.get("boolean", False))

    def answer(self, question, decision=None, mode="agentic"):
        exact = self.exact(question)
        if exact is not None:
            return exact
        if mode == "agentic" and decision:
            return self.agentic(decision)
        return self.graph_first(question)
