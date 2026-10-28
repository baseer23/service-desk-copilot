"""Offline, isolated retrieval evaluation for one saved document.

The harness rebuilds a temporary local index from a document's saved
extraction artifact (never Azure, never the workspace's own stores) and runs
DeskMate's own document identification, graph object selection, local
hydration, local search, evidence selection and de-duplication for each
question in a reference spec. It reports whether answer-bearing evidence
reaches the model, from which path, and whether its page supports an accurate
citation. Model answers themselves need a live, authorized run.

Spec format (JSON): {"document_id", "title", "inventory_decoys": [{id, title}],
"questions": [{"id", "question", "category", "expected": [anchor text...],
"pages": [int], "all_required": bool, "answerable": bool, "follow_up_of": id}]}
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from backend.app.agent.graph import ServiceDeskAgent
from backend.app.services.chunking import split_structured

DEFAULT_TOP_K = 6


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9:|@./]", "", str(value).casefold())


@dataclass
class QuestionResult:
    id: str
    category: str
    question: str
    identified: list[str]
    identification_ok: bool
    graph_objects: int
    evidence_count: int
    duplicates: int
    found: bool
    path: str
    rank: int | None
    evidence_pages: list[int]
    citation_ok: bool
    answerable: bool
    notes: list[str] = field(default_factory=list)
    mislabelled: int = 0

    @property
    def retrieval_pass(self) -> bool:
        if not self.answerable:
            return self.identification_ok
        return self.identification_ok and self.found and self.citation_ok


class _RecordingGraph:
    """Collects the structural objects the real graph publisher would write."""

    def __init__(self):
        self.objects = []

    def upsert_document(self, *args, **kwargs):
        pass

    def upsert_page(self, *args, **kwargs):
        pass

    def upsert_reference_object(self, doc_id, item):
        self.objects.append({"object_id": item["object_id"], "page": item.get("page")})

    def link_reference_objects(self, *args, **kwargs):
        pass

    def link_website_document(self, *args, **kwargs):
        return {"outbound": 0, "inbound": 0}


def build_index(settings, document_id, title, extraction):
    """Index a saved extraction into the temporary stores in ``settings``."""
    from backend.app.services.local_documents import LocalDocumentRepository

    repository = LocalDocumentRepository(settings)
    repository.records.write(
        "document",
        document_id,
        {
            "id": document_id,
            "title": title,
            "source_type": "pdf",
            "status": "ready",
            "phase": "complete",
            "ingestion_mode": "structured_graph",
            "version": 1,
            "local_index_ready": True,
        },
    )
    objects = split_structured(document_id, extraction, settings.chunk_tokens, 1)
    vector = repository.search_store.vector
    embeddings = repository.embeddings.embed_texts([item["text"] for item in objects])
    repository.search_store.upsert(
        [
            {
                "id": item["id"],
                "text": item["text"],
                "embedding": embedding,
                "metadata": {
                    "doc_id": document_id,
                    "title": title,
                    "source_type": "pdf",
                    "page": item["page"],
                    "object_id": item["object_id"],
                    "object_type": item["object_type"],
                    "ingestion_mode": "structured_graph",
                    "embedding_provider": vector.embedding_provider,
                    "embedding_model": vector.embedding_model,
                },
            }
            for item, embedding in zip(objects, embeddings, strict=True)
        ]
    )
    graph = _RecordingGraph()
    repository._publish_reference_objects(graph, document_id, title, extraction, version=1)
    return repository, graph.objects, objects


def _anchor_pages(extraction):
    """Map normalized source text to the page it physically appears on."""
    blocks = [(p.get("page"), p.get("text", "")) for p in extraction.get("paragraphs", [])]
    blocks += [(p.get("page"), p.get("text", "")) for p in extraction.get("pages", [])]
    for table in extraction.get("tables", []):
        rows = {}
        for cell in table.get("cells", []):
            rows.setdefault(cell["row"], []).append(cell.get("text", ""))
        blocks.append(
            (table.get("page"), "\n".join(" | ".join(r) for _, r in sorted(rows.items())))
        )
    return [(page, _normalized(text)) for page, text in blocks if page is not None]


def evaluate(spec, extraction, settings, top_k=DEFAULT_TOP_K):
    """Run every question in ``spec`` against a temporary index of ``extraction``."""
    document_id, title = spec["document_id"], spec["title"]
    inventory = [{"id": document_id, "title": title}, *spec.get("inventory_decoys", [])]
    repository, graph_objects, _ = build_index(settings, document_id, title, extraction)
    source_blocks = _anchor_pages(extraction)
    row = {
        "document_id": document_id,
        "title": title,
        "entities": [],
        "mention_object_ids": [],
        "claim_object_ids": [],
        "page_objects": graph_objects,
    }
    object_ids = ServiceDeskAgent._graph_object_ids([row])
    select = getattr(ServiceDeskAgent, "_select_graph_evidence", None)
    dedupe = getattr(ServiceDeskAgent, "_unique_document_rows", None)
    scope = getattr(ServiceDeskAgent, "_scope_documents", None)
    resolved_by_id, results = {}, []
    for question in spec["questions"]:
        text, notes = question["question"], []
        parent = question.get("follow_up_of")
        context = (
            {
                "document_references": [
                    {"document_id": item} for item in resolved_by_id.get(parent, [])
                ]
            }
            if parent
            else None
        )
        if scope:
            # The classifier is assumed to have chosen saved documents.
            matches, resolved, kind = scope(text, inventory, context, documents_intent=True)
            identified = [match["id"] for match in matches]
        else:
            identified = [
                m["id"] for m in ServiceDeskAgent._document_alias_matches(text, inventory)
            ]
            resolved, kind = identified if len(identified) == 1 else [], "title"
        if len(identified) > 1 and not resolved:
            notes.append(f"{len(identified)} candidate documents ({kind}): clarification asked")
        elif identified and kind != "title":
            notes.append(f"document identified by {kind}")
        resolved_by_id[question["id"]] = resolved or identified
        embedding = repository.embeddings.embed_texts([text])[0]
        graph_rows = []
        if document_id in resolved:
            graph_rows = repository.search_store.resolve_object_ids(object_ids, embedding)
            if select:
                graph_rows = select(graph_rows, text, top_k)
            for item in graph_rows:
                item["match_type"] = "graph_reference"
        local_rows = repository.search_store.hybrid(text, embedding, top_k)
        merged = [*graph_rows, *local_rows]
        combined = dedupe(merged) if dedupe else merged
        duplicates = len(combined) - len(
            {(item.get("metadata", {}).get("doc_id"), item.get("id")) for item in combined}
        )
        anchors = [(value, _normalized(value)) for value in question.get("expected", [])]
        answerable = question.get("answerable", True)
        hits, mislabelled = [], 0
        for rank, item in enumerate(combined, 1):
            body, stored = _normalized(item.get("text", "")), int(item["metadata"].get("page") or 0)
            for value, anchor in anchors:
                if not anchor or anchor not in body:
                    continue
                true_pages = {page for page, block in source_blocks if anchor in block}
                correct = stored in true_pages
                mislabelled += not correct
                hits.append((rank, item, value, stored, correct))
        covered = {value for _, _, value, _, _ in hits}
        cited = {value for _, _, value, _, correct in hits if correct}
        required = [value for value, _ in anchors]
        found = (set(required) <= covered) if question.get("all_required") else bool(covered)
        citation_ok = (set(required) <= cited) if question.get("all_required") else bool(cited)
        if not answerable:
            found, citation_ok = False, True
        if answerable and not found:
            notes.append("missing: " + ", ".join(sorted(set(required) - covered)))
        elif answerable and not citation_ok:
            wrong = sorted({f"{value}@p{stored}" for _, _, value, stored, ok in hits if not ok})
            notes.append("stored under the wrong page: " + ", ".join(wrong))
        first = min(hits, key=lambda hit: hit[0]) if hits else None
        path = (
            ("graph" if first[1].get("match_type") == "graph_reference" else "local")
            if first
            else "none"
        )
        results.append(
            QuestionResult(
                id=question["id"],
                category=question.get("category", ""),
                question=text,
                identified=identified,
                identification_ok=document_id in identified,
                graph_objects=len(object_ids) if document_id in resolved else 0,
                evidence_count=len(combined),
                duplicates=duplicates,
                found=found,
                path=path,
                rank=first[0] if first else None,
                evidence_pages=sorted({stored for _, _, _, stored, _ in hits}),
                citation_ok=citation_ok,
                answerable=answerable,
                notes=notes,
            )
        )
        results[-1].mislabelled = mislabelled
    return results


def render(results) -> str:
    lines = [
        "| ID | Category | Doc identified | Graph objects | Evidence (dupes) | Answer evidence | "
        "Path / rank | Stored page(s) | Mislabelled | Citation | Retrieval | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        lines.append(
            f"| {item.id} | {item.category} | {'yes' if item.identification_ok else 'NO'} | "
            f"{item.graph_objects} | {item.evidence_count} ({item.duplicates}) | "
            f"{'found' if item.found else ('n/a' if not item.answerable else 'MISSING')} | "
            f"{item.path} / {item.rank or '-'} | {item.evidence_pages or '-'} | "
            f"{item.mislabelled} | {'ok' if item.citation_ok else 'WRONG'} | "
            f"{'PASS' if item.retrieval_pass else 'FAIL'} | "
            f"{'; '.join(item.notes)} |"
        )
    passed = sum(item.retrieval_pass for item in results)
    lines.append(f"\nRetrieval PASS {passed}/{len(results)}")
    return "\n".join(lines)


def temporary_settings(root: Path | None = None, **overrides):
    """Settings whose stores live in a fresh temporary directory (no .env)."""
    from backend.app.core.config import Settings

    root = Path(root or tempfile.mkdtemp(prefix="deskmate-eval-"))
    values = {
        "test_mode": True,
        "generation_provider": "stub",
        "embed_provider": "sentence_transformers",
        "local_persistence_enabled": True,
        "data_platform": "local",
        "chroma_dir": root / "chroma",
        "case_db_path": root / "state.sqlite3",
        "local_documents_dir": root / "documents",
        "neo4j_uri": "",
        **overrides,
    }
    return Settings(_env_file=None, **values)
