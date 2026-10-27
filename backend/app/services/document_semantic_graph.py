"""Authorized asynchronous orchestration for saved-document semantic graph jobs."""

import json

from backend.app.models.provider import LLMError
from backend.app.services.document_graph_understanding import DocumentGraphUnderstanding
from backend.app.services.graph_placement import GraphPlacementService
from backend.app.services.graph_discovery import GraphDiscovery
from backend.app.models.document_graph_models import GraphPlacementPlan


class DocumentSemanticGraphService:
    def __init__(self, settings, repository, records, provider, graph, vector_store):
        self.settings, self.repository, self.records = settings, repository, records
        self.provider, self.graph, self.vector_store = provider, graph, vector_store

    def _status(self, document_id, status, **extra):
        row = self.records.read("document", document_id)
        if not row:
            raise ValueError("Document not found")
        state = {**row["payload"], "semantic_graph_status": status, **extra}
        if status != "failed" and state.get("local_index_ready"):
            for key in (
                "processing_error_code", "processing_error_reason",
                "processing_error_type", "failed_stage",
            ):
                state.pop(key, None)
        self.records.write("document", document_id, state, previous=row, fields={"status": state["status"]})
        return state

    async def enrich(self, document_id):
        try:
            state = self.repository.get(document_id)
            if state.get("semantic_graph_status") == "linked": return state
            if not state.get("local_index_ready") or state.get("graph_link_status") != "linked":
                return self._status(document_id, "pending", semantic_graph_reason="Local index or structural graph is not ready")
            self._status(document_id, "understanding")
            extracted = json.loads(self.repository.blobs.get(document_id, state.get("extraction_artifact", "extraction.json")))
            understanding = DocumentGraphUnderstanding(self.settings, self.provider, self.records)
            proposal, partial = await understanding.understand_document(document_id, extracted, state)
            if partial:
                return self._status(
                    document_id, "partial", semantic_graph_summary=proposal.summary[:4000],
                    semantic_graph_retryable=False,
                    semantic_graph_reason="Configured model-call budget was exhausted",
                )
            self._status(document_id, "discovering", semantic_graph_summary=proposal.summary[:4000])
            key = understanding.job_key(document_id, state)
            job_row = self.records.read("document_semantic_graph_job", key); job = dict(job_row["payload"])
            discovery = job.get("graph_candidates") or GraphDiscovery(self.graph).discover(proposal)
            if not job.get("graph_candidates"):
                job["graph_candidates"] = discovery; job["status"] = "discovered"
                job_row = self.records.write("document_semantic_graph_job", key, job, previous=job_row)
            placement = GraphPlacementService(self.provider, self.graph, self.vector_store, self.records)
            if job.get("placement_plan"):
                plan = GraphPlacementPlan.model_validate(job["placement_plan"])
            else:
                plan = await placement.prepare_placement(proposal, discovery=discovery)
                job = dict(job_row["payload"])
                job["placement_plan"] = plan.model_dump(); job["status"] = "placing"
                self.records.write("document_semantic_graph_job", key, job, previous=job_row)
            self._status(document_id, "placing")
            result = placement.apply_placement(plan, proposal)
            self.graph.update_document_semantics(document_id, document_type=proposal.document_type, summary=proposal.summary, status="linked")
            final = self._status(document_id, "linked", semantic_graph_result=result,
                                 semantic_graph_retryable=False, semantic_graph_reason=None,
                                 document_type=proposal.document_type, semantic_graph_summary=proposal.summary[:4000])
            job_row = self.records.read("document_semantic_graph_job", key); job = dict(job_row["payload"])
            job["status"] = "linked"; job["result"] = result
            self.records.write("document_semantic_graph_job", key, job, previous=job_row)
            return final
        except Exception as exc:
            # Only provider failures explicitly declared recoverable are safe to
            # resume automatically. Graph mutations and ambiguous model failures
            # remain terminal until an operator reviews them.
            retryable = isinstance(exc, LLMError) and exc.recoverable
            self._status(
                document_id, "failed", semantic_graph_reason=type(exc).__name__,
                semantic_graph_retryable=retryable,
            )
            raise
