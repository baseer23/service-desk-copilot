"""Tenant-owned semantic memory with durable, bounded graph delivery."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from backend.app.agent.models import (
    SemanticEntity,
    SemanticFact,
    SemanticMemoryBatch,
    SemanticSource,
)
from backend.app.core.principal import Principal, current_principal
from backend.app.store.local_records import LocalRecords

# Python's re does not support Unicode properties. This deliberately rejects
# control characters/digits while retaining ordinary international names.
_NAME_ASSERTION = re.compile(
    r"(?:^|[.!?]\s*)(?:no[, ]+)?(?:please\s+)?(?:remember\s+that\s+)?"
    r"(?:my name is|call me)\s+([^\W\d_][^\d\n\r.!?,]{0,119})(?=$|[.!?,])",
    re.IGNORECASE | re.UNICODE,
)


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


class SemanticMemoryService:
    """Validate explicit identity assertions and preserve graph writes locally."""

    def __init__(self, settings, records: LocalRecords | None = None, graph=None):
        self.settings = settings
        self.records = records or LocalRecords(settings)
        self.graph = graph

    def capture_conversation(
        self,
        text: str,
        conversation_id: str,
        *,
        principal: Principal | None = None,
        allow_personal_memory: bool = True,
    ) -> dict:
        actor = principal or current_principal(self.settings)
        if not allow_personal_memory:
            return {"captured": False, "reason": "personal_memory_disabled"}
        match = _NAME_ASSERTION.search((text or "").strip())
        if not match:
            return {"captured": False, "reason": "no_explicit_identity_assertion"}
        name = " ".join(match.group(1).split()).strip(" '\"’-–")
        if not 1 <= len(name) <= 120:
            return {"captured": False, "reason": "invalid_identity_value"}
        observed = datetime.now(timezone.utc).isoformat()
        source_hash = _digest(text)
        source_id = _digest(actor.tenant_id, actor.owner_id, conversation_id, source_hash)[:40]
        user_id = _digest(actor.tenant_id, actor.owner_id)[:40]
        prior = self.profile(principal=actor).get("name_fact")
        if prior and prior["value"].casefold() == name.casefold():
            return {"captured": False, "reason": "duplicate", "fact": prior}
        fact_id = _digest(actor.tenant_id, actor.owner_id, "NAME", name.casefold())[:40]
        source = SemanticSource(
            id=source_id,
            source_type="conversation",
            source_id=conversation_id,
            content_hash=source_hash,
            observed_at=observed,
            excerpt=match.group(0).strip(),
        )
        fact = SemanticFact(
            id=fact_id,
            subject_id=user_id,
            predicate="NAME",
            value=name,
            tenant_id=actor.tenant_id,
            owner_id=actor.owner_id,
            confidence=1,
            source_id=source_id,
            supersedes_id=prior["id"] if prior else None,
        )
        batch = SemanticMemoryBatch(
            entities=[SemanticEntity(
                id=user_id, entity_type="User", name=name,
                tenant_id=actor.tenant_id, owner_id=actor.owner_id,
            )],
            facts=[fact], sources=[source],
        )
        if prior:
            old = self.records.read("semantic_fact", prior["id"], tenant=actor.tenant_id)
            if old:
                old_payload = {**old["payload"], "status": "superseded", "superseded_by": fact.id}
                self.records.write(
                    "semantic_fact", prior["id"], old_payload, previous=old,
                    tenant=actor.tenant_id,
                    fields={"owner_id": actor.owner_id, "status": "superseded"},
                )
        payload = {**fact.model_dump(), "observed_at": observed}
        self.records.write(
            "semantic_fact", fact.id, payload, tenant=actor.tenant_id,
            fields={"owner_id": actor.owner_id, "status": "confirmed"},
        )
        job = {
            "batch": batch.model_dump(), "status": "pending", "attempts": 0,
            "owner_id": actor.owner_id, "created_at": observed, "last_error": None,
        }
        self.records.write(
            "semantic_graph_job", fact.id, job, tenant=actor.tenant_id,
            fields={"owner_id": actor.owner_id, "status": "pending"},
        )
        linked = self.deliver(fact.id, principal=actor)
        return {"captured": True, "graph_status": "linked" if linked else "graph_pending", "fact": payload}

    def deliver(self, fact_id: str, *, principal: Principal | None = None) -> bool:
        actor = principal or current_principal(self.settings)
        job = self.records.read("semantic_graph_job", fact_id, tenant=actor.tenant_id)
        if not job or job.get("owner_id") != actor.owner_id or job["payload"]["status"] == "linked":
            return bool(job and job["payload"]["status"] == "linked")
        if self.graph is None or not getattr(self.graph, "available", False):
            return False
        payload = job["payload"]
        if payload["attempts"] >= 3:
            return False
        try:
            self.graph.upsert_semantic_memory(SemanticMemoryBatch.model_validate(payload["batch"]))
            payload = {**payload, "status": "linked", "attempts": payload["attempts"] + 1}
        except Exception as exc:  # bounded durable retry; never expose remote details
            payload = {
                **payload, "status": "retryable_failure",
                "attempts": payload["attempts"] + 1, "last_error": type(exc).__name__,
            }
        self.records.write(
            "semantic_graph_job", fact_id, payload, previous=job, tenant=actor.tenant_id,
            fields={"owner_id": actor.owner_id, "status": payload["status"]},
        )
        return payload["status"] == "linked"

    def profile(self, *, principal: Principal | None = None) -> dict:
        actor = principal or current_principal(self.settings)
        rows = self.records.query(
            "semantic_fact", tenant=actor.tenant_id,
            fields={"owner_id": actor.owner_id, "status": "confirmed"}, limit=100,
        )
        facts = [row["payload"] for row in rows if row.get("owner_id") == actor.owner_id]
        name = next((fact for fact in facts if fact["predicate"] == "NAME"), None)
        jobs = self.records.query(
            "semantic_graph_job", tenant=actor.tenant_id,
            fields={"owner_id": actor.owner_id}, limit=100,
        )
        pending_ids = {
            row["key"] for row in jobs if row["payload"].get("status") != "linked"
        }
        return {
            "owner_id": actor.owner_id, "name": name["value"] if name else None,
            "name_fact": name, "facts": facts,
            "graph_status": "graph_pending"
            if any(fact["id"] in pending_ids for fact in facts) else "linked",
        }

    def retry_pending(self, *, principal: Principal | None = None, limit: int = 10) -> dict:
        actor = principal or current_principal(self.settings)
        jobs = self.records.query(
            "semantic_graph_job", tenant=actor.tenant_id,
            fields={"owner_id": actor.owner_id}, limit=min(max(limit, 1), 20),
        )
        attempted = linked = 0
        for job in jobs:
            if job["payload"].get("status") == "linked" or job["payload"].get("attempts", 0) >= 3:
                continue
            attempted += 1
            linked += int(self.deliver(job["key"], principal=actor))
        return {"attempted": attempted, "linked": linked, "remaining": attempted - linked}

    def search(self, query: str, limit: int = 10, *, principal: Principal | None = None) -> dict:
        actor = principal or current_principal(self.settings)
        if not query.strip() or not 1 <= limit <= 20:
            raise ValueError("query and a limit between 1 and 20 are required")
        profile = self.profile(principal=actor)
        confirmed_ids = {fact["id"] for fact in profile["facts"]}
        if self.graph is not None and getattr(self.graph, "available", False):
            try:
                rows = [
                    row for row in self.graph.search_semantic_memory(actor.owner_id, query, limit)
                    if row.get("id") in confirmed_ids
                ]
                if rows:
                    return {"facts": rows, "graph_status": "linked"}
            except Exception:
                pass
        terms = query.casefold().split()[:10]
        rows = [
            fact for fact in profile["facts"]
            if any(term in (fact["predicate"] + " " + fact["value"]).casefold() for term in terms)
        ][:limit]
        return {"facts": rows, "graph_status": profile["graph_status"]}

    def forget_profile(self, *, principal: Principal | None = None) -> int:
        actor = principal or current_principal(self.settings)
        rows = self.records.query("semantic_fact", tenant=actor.tenant_id, fields={"owner_id": actor.owner_id})
        changed = 0
        for row in rows:
            payload = {**row["payload"], "status": "revoked"}
            self.records.write(
                "semantic_fact", row["key"], payload, previous=row, tenant=actor.tenant_id,
                fields={"owner_id": actor.owner_id, "status": "revoked"},
            )
            job = self.records.read("semantic_graph_job", row["key"], tenant=actor.tenant_id)
            if job and job.get("owner_id") == actor.owner_id:
                job_payload = dict(job["payload"])
                batch = dict(job_payload["batch"])
                batch["facts"] = [
                    {**fact, "status": "revoked"} for fact in batch.get("facts", [])
                ]
                job_payload.update(batch=batch, status="pending", attempts=0, last_error=None)
                self.records.write(
                    "semantic_graph_job", row["key"], job_payload, previous=job,
                    tenant=actor.tenant_id,
                    fields={"owner_id": actor.owner_id, "status": "pending"},
                )
                self.deliver(row["key"], principal=actor)
            changed += 1
        return changed


class AuthorizedSemanticGraph:
    """Short-lived, no-SDK-retry writer enabled only by explicit ingestion config."""

    def __init__(self, settings):
        self.settings = settings
        self.available = bool(
            settings.neo4j_ingestion_enabled and settings.neo4j_uri
            and settings.neo4j_ingestion_user
            and settings.neo4j_ingestion_password.get_secret_value()
        )

    def upsert_semantic_memory(self, batch):
        if not self.available:
            raise RuntimeError("Semantic graph writer is not authorized")
        from neo4j import GraphDatabase
        from backend.app.store.graph_repo import GraphRepository

        driver = GraphDatabase.driver(
            self.settings.neo4j_uri,
            auth=(
                self.settings.neo4j_ingestion_user,
                self.settings.neo4j_ingestion_password.get_secret_value(),
            ),
            connection_timeout=5,
            connection_acquisition_timeout=5,
            max_transaction_retry_time=0,
        )
        try:
            repo = GraphRepository(
                driver,
                tenant_provider=lambda: current_principal(self.settings).tenant_id,
                optional=False,
                allow_fixtures=False,
                database=self.settings.neo4j_database,
            )
            repo.ensure_constraints()
            repo.upsert_semantic_memory(batch)
        finally:
            driver.close()

    def search_semantic_memory(self, owner_id: str, query: str, limit: int = 10):
        if not self.available:
            return []
        from neo4j import GraphDatabase
        from backend.app.store.graph_repo import GraphRepository

        driver = GraphDatabase.driver(
            self.settings.neo4j_uri,
            auth=(
                self.settings.neo4j_ingestion_user,
                self.settings.neo4j_ingestion_password.get_secret_value(),
            ),
            connection_timeout=5,
            connection_acquisition_timeout=5,
            max_transaction_retry_time=0,
        )
        try:
            return GraphRepository(
                driver,
                tenant_provider=lambda: current_principal(self.settings).tenant_id,
                optional=False,
                allow_fixtures=False,
                database=self.settings.neo4j_database,
            ).search_semantic_memory(owner_id, query, limit)
        finally:
            driver.close()
