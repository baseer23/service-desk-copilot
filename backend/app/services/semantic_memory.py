"""Tenant-owned semantic memory with durable, bounded graph delivery."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from backend.app.agent.models import (
    SemanticEntity,
    SemanticFact,
    SemanticMemoryBatch,
    SemanticRelationship,
    SemanticSource,
)
from backend.app.core.principal import Principal, current_principal
from backend.app.store.local_records import LocalRecords
from backend.app.store.vector_chroma import StorageError, VectorChromaStore
from backend.app.adapters.embeddings import get_embedding_provider

# Python's re does not support Unicode properties. This deliberately rejects
# control characters/digits while retaining ordinary international names.
_NAME_ASSERTION = re.compile(
    r"(?:^|[.!?]\s*)(?:no[, ]+)?(?:please\s+)?(?:remember\s+that\s+)?"
    r"(?:my name is|call me)\s+([^\W\d_][^\d\n\r.!?,]{0,119})(?=$|[.!?,])",
    re.IGNORECASE | re.UNICODE,
)
_REMEMBER = re.compile(
    r"^(?:(?:actually|no)[, ]+)?(?:please\s+)?(?:remember(?:\s+that)?|save\s+this\s+about\s+me|keep\s+in\s+mind(?:\s+that)?)\s+(.+?)[.!?]*$",
    re.IGNORECASE,
)
_PREFERENCE = re.compile(r"^i\s+prefer\s+(.+?)(?:\s+for\s+.+)?$", re.IGNORECASE)
_PROJECT = re.compile(r"^(.+?)\s+is\s+my\s+(?:main|current)\s+project$", re.IGNORECASE)
_USES = re.compile(r"^i\s+use\s+(.+)$", re.IGNORECASE)
_WORKS_AT = re.compile(r"^i\s+work\s+at\s+(.+)$", re.IGNORECASE)


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


class SemanticMemoryService:
    """Validate explicit identity assertions and preserve graph writes locally."""

    def __init__(self, settings, records: LocalRecords | None = None, graph=None, vector=None, embeddings=None):
        self.settings = settings
        self.records = records or LocalRecords(settings)
        self.graph = graph
        self.embeddings = embeddings or get_embedding_provider(settings)
        if vector is not None:
            self.vector = vector
        else:
            identity = f"{settings.embed_provider}:{getattr(self.embeddings, 'model_name', settings.embed_model_name)}:384:normalized-v1"
            name = "personal_memory_local_" + hashlib.sha256(identity.encode()).hexdigest()[:16]
            try:
                self.vector = VectorChromaStore(
                    str(settings.chroma_dir), collection_name=name,
                    embedding_provider=settings.embed_provider,
                    embedding_model=getattr(self.embeddings, "model_name", settings.embed_model_name),
                    embedding_dimensions=384,
                )
            except StorageError:
                self.vector = None

    @staticmethod
    def _parse(text: str):
        stripped = " ".join((text or "").strip().split())
        name = _NAME_ASSERTION.search(stripped)
        if name:
            return "identity", "NAME", name.group(1).strip(" '\"’-–")
        explicit = _REMEMBER.match(stripped)
        if not explicit:
            return None
        statement = explicit.group(1).strip(" '\"’-–")
        for pattern, category, predicate in (
            (_PREFERENCE, "preference", "PREFERRED_CLOUD"),
            (_PROJECT, "project", "CURRENT_PROJECT"),
            (_WORKS_AT, "employment", "WORKS_AT"),
            (_USES, "technology", "USES"),
        ):
            match = pattern.match(statement)
            if match:
                value = match.group(1).strip(" '\"’-–")
                if predicate == "PREFERRED_CLOUD":
                    value = re.sub(r"\s+instead$", "", value, flags=re.IGNORECASE)
                return category, predicate, value
        return "personal_note", "PERSONAL_NOTE", statement

    @staticmethod
    def _index_text(fact):
        labels = {
            "NAME": "User identity: name is",
            "PREFERRED_CLOUD": "User preference: preferred cloud platform is",
            "CURRENT_PROJECT": "User project: current main project is",
            "WORKS_AT": "User employment: works at",
            "USES": "User technology: uses",
            "PERSONAL_NOTE": "User personal note:",
        }
        return f"{labels.get(fact['predicate'], 'User fact:')} {fact['value']}."

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
        parsed = self._parse(text)
        if not parsed:
            return {"captured": False, "reason": "no_explicit_memory_instruction"}
        category, predicate, value = parsed
        if not 1 <= len(value) <= 500:
            return {"captured": False, "reason": "invalid_memory_value"}
        observed = datetime.now(timezone.utc).isoformat()
        source_hash = _digest(text)
        source_id = _digest(actor.tenant_id, actor.owner_id, conversation_id, source_hash)[:40]
        user_id = _digest(actor.tenant_id, actor.owner_id)[:40]
        prior = next((fact for fact in self.profile(principal=actor)["facts"] if fact["predicate"] == predicate), None)
        if prior and prior["value"].casefold() == value.casefold():
            return {"captured": False, "reason": "duplicate", "fact": prior}
        fact_id = _digest(actor.tenant_id, actor.owner_id, predicate, value.casefold())[:40]
        source = SemanticSource(
            id=source_id,
            source_type="conversation",
            source_id=conversation_id,
            content_hash=source_hash,
            observed_at=observed,
            excerpt=text.strip(), owner_id=actor.owner_id,
        )
        fact = SemanticFact(
            id=fact_id,
            subject_id=user_id,
            predicate=predicate, category=category,
            value=value,
            tenant_id=actor.tenant_id,
            owner_id=actor.owner_id,
            confidence=1,
            source_id=source_id,
            supersedes_id=prior["id"] if prior else None,
        )
        entities = [SemanticEntity(
                id=user_id, entity_type="User", name=value if predicate == "NAME" else actor.owner_id,
                tenant_id=actor.tenant_id, owner_id=actor.owner_id,
            )]
        relationships = []
        relation_types = {
            "USES": ("Technology", "USES"),
            "WORKS_AT": ("Organization", "WORKS_AT"),
            "CURRENT_PROJECT": ("Project", "RELATED_TO"),
        }
        if predicate in relation_types:
            entity_type, relationship = relation_types[predicate]
            target_id = _digest(actor.tenant_id, actor.owner_id, entity_type, value.casefold())[:40]
            entities.append(SemanticEntity(
                id=target_id, entity_type=entity_type, name=value,
                tenant_id=actor.tenant_id, owner_id=actor.owner_id,
            ))
            relationships.append(SemanticRelationship(
                id=_digest(fact.id, relationship)[:40], source_entity_id=user_id,
                relationship=relationship, target_entity_id=target_id, fact_id=fact.id,
                source_id=source_id, confidence=1,
            ))
        batch = SemanticMemoryBatch(
            entities=entities, facts=[fact], sources=[source], relationships=relationships
        )
        payload = {**fact.model_dump(), "observed_at": observed}
        job = {"batch": batch.model_dump(), "status": "pending", "attempts": 0, "owner_id": actor.owner_id, "created_at": observed, "last_error": None}
        vector_job = {"fact_id": fact.id, "status": "pending", "attempts": 0, "owner_id": actor.owner_id, "last_error": None}
        with self.records.transaction():
            if prior:
                old = self.records.read("semantic_fact", prior["id"], tenant=actor.tenant_id)
                if old:
                    self.records.write("semantic_fact", prior["id"], {**old["payload"], "status": "superseded", "superseded_by": fact.id}, previous=old, tenant=actor.tenant_id, fields={"owner_id": actor.owner_id, "status": "superseded"})
            self.records.write("semantic_source", source.id, source.model_dump(), tenant=actor.tenant_id, fields={"owner_id": actor.owner_id})
            self.records.write("semantic_fact", fact.id, payload, tenant=actor.tenant_id, fields={"owner_id": actor.owner_id, "status": "confirmed"})
            self.records.write("semantic_vector_job", fact.id, vector_job, tenant=actor.tenant_id, fields={"owner_id": actor.owner_id, "status": "pending"})
            self.records.write("semantic_graph_job", fact.id, job, tenant=actor.tenant_id, fields={"owner_id": actor.owner_id, "status": "pending"})
        if prior:
            self._remove_vector(prior["id"])
        indexed = self.index(fact.id, principal=actor)
        linked = self.deliver(fact.id, principal=actor)
        return {"captured": True, "saved": True, "local_status": "saved", "vector_status": "indexed" if indexed else "index_pending", "graph_status": "linked" if linked else "graph_pending", "fact": payload}

    def _remove_vector(self, fact_id):
        if self.vector:
            try:
                self.vector.delete([fact_id])
            except StorageError:
                pass

    def index(self, fact_id: str, *, principal: Principal | None = None) -> bool:
        actor = principal or current_principal(self.settings)
        job = self.records.read("semantic_vector_job", fact_id, tenant=actor.tenant_id)
        fact = self.records.read("semantic_fact", fact_id, tenant=actor.tenant_id)
        if not job or not fact or fact.get("owner_id") != actor.owner_id or not self.vector:
            return False
        if fact["payload"].get("status") != "confirmed":
            self._remove_vector(fact_id)
            return False
        try:
            content = self._index_text(fact["payload"])
            vector = self.embeddings.embed_texts([content])[0]
            meta = {"fact_id": fact_id, "tenant_id": actor.tenant_id, "owner_scope": actor.owner_id, "predicate": fact["payload"]["predicate"], "category": fact["payload"].get("category", "personal_note"), "source_id": fact["payload"]["source_id"], "observed_at": fact["payload"]["observed_at"], "content_hash": _digest(content), "schema_version": 1, "embedding_provider": self.settings.embed_provider, "embedding_model": getattr(self.embeddings, "model_name", self.settings.embed_model_name)}
            self.vector.upsert([{"id": fact_id, "text": content, "metadata": meta, "embedding": vector}])
            status, error = "indexed", None
        except Exception as exc:
            status, error = "retryable_failure", type(exc).__name__
        body = {**job["payload"], "status": status, "attempts": job["payload"].get("attempts", 0) + 1, "last_error": error}
        self.records.write("semantic_vector_job", fact_id, body, previous=job, tenant=actor.tenant_id, fields={"owner_id": actor.owner_id, "status": status})
        return status == "indexed"

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
        # Older confirmed name facts predate vector jobs. Create only the
        # missing idempotent jobs; authoritative fact payloads are unchanged.
        for fact in self.records.query(
            "semantic_fact",
            tenant=actor.tenant_id,
            fields={"owner_id": actor.owner_id, "status": "confirmed"},
            limit=min(max(limit, 1), 20),
        ):
            if self.records.read("semantic_vector_job", fact["key"], tenant=actor.tenant_id):
                continue
            self.records.write(
                "semantic_vector_job",
                fact["key"],
                {
                    "fact_id": fact["key"],
                    "status": "pending",
                    "attempts": 0,
                    "owner_id": actor.owner_id,
                    "last_error": None,
                },
                tenant=actor.tenant_id,
                fields={"owner_id": actor.owner_id, "status": "pending"},
                create_only=True,
            )
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
        vector_jobs = self.records.query("semantic_vector_job", tenant=actor.tenant_id, fields={"owner_id": actor.owner_id}, limit=min(max(limit, 1), 20))
        vector_attempted = vector_indexed = 0
        for job in vector_jobs:
            if job["payload"].get("status") == "indexed" or job["payload"].get("attempts", 0) >= 3:
                continue
            vector_attempted += 1
            vector_indexed += int(self.index(job["key"], principal=actor))
        return {"attempted": attempted, "linked": linked, "remaining": attempted - linked, "vector_attempted": vector_attempted, "vector_indexed": vector_indexed}

    def search(self, query: str, limit: int = 10, *, principal: Principal | None = None) -> dict:
        actor = principal or current_principal(self.settings)
        if not query.strip() or not 1 <= limit <= 20:
            raise ValueError("query and a limit between 1 and 20 are required")
        profile = self.profile(principal=actor)
        confirmed_ids = {fact["id"] for fact in profile["facts"]}
        found = []
        if self.vector is not None:
            try:
                query_vector = self.embeddings.embed_texts([query])[0]
                for row in self.vector.search(query_vector, min(limit * 3, 20)):
                    meta = row.get("metadata", {})
                    if (
                        meta.get("tenant_id") != actor.tenant_id
                        or meta.get("owner_scope") != actor.owner_id
                        or row["id"] not in confirmed_ids
                        or float(row.get("score", 1)) > 0.75
                    ):
                        continue
                    authoritative = self.records.read("semantic_fact", row["id"], tenant=actor.tenant_id)
                    if authoritative and authoritative.get("owner_id") == actor.owner_id and authoritative["payload"].get("status") == "confirmed":
                        found.append({**authoritative["payload"], "distance": row.get("score")})
                if found:
                    return {"facts": found[:limit], "graph_status": profile["graph_status"], "vector_status": "indexed"}
            except Exception:
                pass
        normalized = query.casefold()
        aliases = {
            "PREFERRED_CLOUD": ("cloud", "platform", "prefer", "deployment"),
            "CURRENT_PROJECT": ("project", "working on", "main work"),
            "NAME": ("name", "call me", "who am i"),
            "USES": ("use", "technology", "stack", "tool"),
            "WORKS_AT": ("work", "employer", "company"),
        }
        terms = [
            term.strip("?.!,") for term in normalized.split()[:12]
            if len(term.strip("?.!,")) >= 3
            and term.strip("?.!,") not in {"what", "which", "that", "this", "the", "about", "does"}
        ]
        rows = [
            fact for fact in profile["facts"]
            if any(term in (fact["predicate"] + " " + fact["value"]).casefold() for term in terms)
            or any(alias in normalized for alias in aliases.get(fact["predicate"], ()))
        ][:limit]
        return {"facts": rows, "graph_status": profile["graph_status"], "vector_status": "fallback"}

    def forget(self, fact_id: str, *, principal: Principal | None = None) -> bool:
        actor = principal or current_principal(self.settings)
        row = self.records.read("semantic_fact", fact_id, tenant=actor.tenant_id)
        if not row or row.get("owner_id") != actor.owner_id or row["payload"].get("status") != "confirmed":
            return False
        self.records.write("semantic_fact", fact_id, {**row["payload"], "status": "revoked"}, previous=row, tenant=actor.tenant_id, fields={"owner_id": actor.owner_id, "status": "revoked"})
        self._remove_vector(fact_id)
        job = self.records.read("semantic_graph_job", fact_id, tenant=actor.tenant_id)
        if job:
            batch = {**job["payload"]["batch"], "facts": [{**fact, "status": "revoked"} for fact in job["payload"]["batch"].get("facts", [])]}
            body = {**job["payload"], "batch": batch, "status": "pending", "attempts": 0, "last_error": None}
            self.records.write("semantic_graph_job", fact_id, body, previous=job, tenant=actor.tenant_id, fields={"owner_id": actor.owner_id, "status": "pending"})
            self.deliver(fact_id, principal=actor)
        return True

    def forget_profile(self, *, principal: Principal | None = None) -> int:
        actor = principal or current_principal(self.settings)
        rows = self.records.query("semantic_fact", tenant=actor.tenant_id, fields={"owner_id": actor.owner_id})
        changed = 0
        for row in rows:
            changed += int(self.forget(row["key"], principal=actor))
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
