"""Offline execution of the experiment's Cypher templates (tests only).

This is not a Cypher engine. It recognises exactly the allowlisted template
texts in ``mutations`` and applies their documented semantics (MERGE by id,
MATCH-or-skip, tenant checks), and refuses anything else. A real Neo4j runs
the same text unchanged; see the report for which checks ran against which.
"""

from __future__ import annotations

import json
import time

from backend.app.experiments.probabilistic_kg.mutations import (
    PIPELINE,
    READ_TEMPLATES,
    TEMPLATES,
    scope,
)

_BY_TEXT = {text: name for name, text in {**TEMPLATES, **READ_TEMPLATES}.items()}


class UnsupportedQuery(ValueError):
    pass


class TemplateGraphStore:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}
        self.statements_run = 0

    # -- seeding the structural graph dev already writes ---------------------
    def load_structure(self, index, tenant):
        for document_id, graph in index.structures.items():
            self._node(scope(tenant, document_id), "Document", tenant_id=tenant,
                       original_id=document_id, title=index.titles.get(document_id))
            for node_id, node in graph.nodes.items():
                self._node(scope(tenant, node_id), node["label"], tenant_id=tenant,
                           original_id=node_id, document_id=document_id, page=node.get("page"))
            for source, relationship, target in graph.edges:
                self.edges[(scope(tenant, source), relationship, scope(tenant, target), "")] = {}

    def structure_seed(self):
        return {"nodes": {key: {"labels": sorted(value["labels"]), "props": value["props"]}
                          for key, value in self.nodes.items()},
                "edges": [list(key) for key in self.edges]}

    @classmethod
    def from_seed(cls, seed):
        store = cls()
        for key, value in seed.get("nodes", {}).items():
            store.nodes[key] = {"labels": set(value["labels"]), "props": dict(value["props"])}
        for edge in seed.get("edges", []):
            store.edges[tuple(edge)] = {}
        return store

    def _node(self, node_id, label, **props):
        node = self.nodes.setdefault(node_id, {"labels": set(), "props": {"id": node_id}})
        node["labels"].add(label)
        node["props"].update(props)
        return node

    def _has(self, node_id, label, tenant):
        node = self.nodes.get(node_id)
        return bool(node and label in node["labels"] and node["props"].get("tenant_id") == tenant)

    def _merge_node(self, node_id, label, tenant, values):
        created = node_id not in self.nodes or label not in self.nodes[node_id]["labels"]
        node = self._node(node_id, label)
        if created:
            node["props"]["created_at"] = int(time.time() * 1000)
        node["props"].update({**values, "tenant_id": tenant, "pipeline": PIPELINE})
        return node

    def _edge(self, source, relationship, target, merge_key="", **props):
        self.edges.setdefault((source, relationship, target, merge_key), {}).update(props)

    # -- execution -------------------------------------------------------------
    def run(self, query, params):
        name = _BY_TEXT.get(query)
        if name is None:
            raise UnsupportedQuery("Only the experiment's allowlisted Cypher templates are accepted")
        self.statements_run += 1
        tenant = params.get("tenant")
        rows = params.get("rows", [])
        written = 0
        if name == "entities":
            for row in rows:
                self._merge_node(row["id"], "Entity", tenant, {
                    key: row[key] for key in ("original_id", "name", "aliases", "description")
                } | {"normalized_name": row["normalized"], "kind": row["kind"],
                     "identifiers_json": row["identifiers_json"], "entity_key": row["key"]})
                written += 1
        elif name == "mentions":
            for row in rows:
                if not (self._has(row["document"], "Document", tenant)
                        and self._has(row["entity"], "Entity", tenant)):
                    continue
                self._merge_node(row["id"], "GraphMention", tenant, {
                    "original_id": row["original_id"], "name": row["name"], "kind": row["kind"],
                    "status": "resolved",
                })
                self._edge(row["id"], "MENTIONED_IN", row["document"])
                self._edge(row["id"], "REFERS_TO", row["entity"])
                written += 1
        elif name in {"claims", "hypotheses"}:
            label = "GraphClaim" if name == "claims" else "GraphHypothesis"
            for row in rows:
                if not (self._has(row["subject"], "Entity", tenant)
                        and self._has(row["object"], "Entity", tenant)
                        and self._has(row["document"], "Document", tenant)):
                    continue
                values = {key: row[key] for key in (
                    "original_id", "predicate", "confidence", "rationale", "proposed_kind",
                )}
                if label == "GraphClaim":
                    values |= {key: row[key] for key in ("status", "contested", "is_explicit")}
                self._merge_node(row["id"], label, tenant, values)
                self._edge(row["id"], "SUBJECT", row["subject"])
                self._edge(row["id"], "OBJECT", row["object"])
                self._edge(row["id"], "DOCUMENTED_IN" if label == "GraphClaim" else "PROPOSED_IN",
                           row["document"])
                written += 1
        elif name == "related":
            for row in rows:
                if not (self._has(row["subject"], "Entity", tenant)
                        and self._has(row["object"], "Entity", tenant)):
                    continue
                self._edge(row["subject"], "RELATED_TO", row["object"], row["predicate"],
                           predicate=row["predicate"], status=row["status"],
                           contested=row["contested"], pipeline=PIPELINE)
                written += 1
        elif name == "contradictions":
            for row in rows:
                self._merge_node(row["id"], "Contradiction", tenant, {
                    "original_id": row["original_id"], "description": row["description"],
                    "source": row["source"],
                })
                written += 1
        elif name == "involves":
            for row in rows:
                if self._has(row["contradiction"], "Contradiction", tenant) and self._has(
                    row["claim"], "GraphClaim", tenant
                ):
                    self._edge(row["contradiction"], "INVOLVES", row["claim"])
                    written += 1
        elif name.startswith("support:"):
            _, claim_label, evidence_label = name.split(":")
            for row in rows:
                if self._has(row["claim"], claim_label, tenant) and self._has(
                    row["evidence"], evidence_label, tenant
                ):
                    self._edge(row["claim"], "SUPPORTED_BY", row["evidence"])
                    written += 1
        elif name.startswith("verify:"):
            label = name.split(":", 1)[1]
            present = sum(self._has(value, label, tenant) for value in params.get("ids", []))
            return [{"present": present}]
        elif name == "export:nodes":
            rows = [
                {"id": node_id, "labels": sorted(node["labels"]), "props": dict(node["props"])}
                for node_id, node in self.nodes.items()
                if node["props"].get("tenant_id") == tenant
                and node["props"].get("pipeline") == params.get("pipeline")
            ]
            return rows[: params.get("limit", 10000)]
        elif name == "export:edges":
            rows = []
            for (source, relationship, target, _key), props in self.edges.items():
                node = self.nodes.get(source, {"props": {}})
                if node["props"].get("tenant_id") == tenant and node["props"].get(
                    "pipeline"
                ) == params.get("pipeline"):
                    rows.append({"source": source, "type": relationship, "target": target,
                                 "props": dict(props)})
            return rows[: params.get("limit", 10000)]
        return [{"written": written}]


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def single(self):
        return self.rows[0] if self.rows else None

    def data(self):
        return list(self.rows)

    def __iter__(self):
        return iter(self.rows)


class _Tx:
    def __init__(self, store, log):
        self.store, self.log = store, log

    def run(self, query, parameters=None, **kwargs):
        params = {**(parameters or {}), **kwargs}
        self.log.append(query)
        return _Result(self.store.run(query, params))


class FakeDriver:
    """The subset of the neo4j driver API DriverGraphWriter uses."""

    def __init__(self, store):
        self.store = store
        self.transactions = 0
        self.queries: list[str] = []

    def session(self, **_kwargs):
        driver = self

        class _Session:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute_write(self, fn):
                driver.transactions += 1
                return fn(_Tx(driver.store, driver.queries))

            execute_read = execute_write

        return _Session()


def dumps_seed(store):
    return json.dumps(store.structure_seed())
