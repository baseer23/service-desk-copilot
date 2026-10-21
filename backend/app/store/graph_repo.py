from __future__ import annotations

import re
import hashlib
import logging
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    MutableMapping,
    Optional,
    Sequence,
    Set,
    TypeVar,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import is for type checking only
    from neo4j import Driver, Session, Transaction
else:  # pragma: no cover - runtime driver types resolved dynamically
    Driver = Any
    Session = Any
    Transaction = Any

T = TypeVar("T")
ChunkRecord = Dict[str, object]


class GraphRepository:
    """Persistence adapter for document, chunk, and entity metadata in Neo4j."""

    def __init__(
        self, driver: Driver, *, tenant_provider=None, optional=False, allow_fixtures=True,
        database: str | None = None,
    ) -> None:
        """Store the Neo4j driver for subsequent transactional work."""
        self._driver: Driver = driver
        self.tenant_provider = tenant_provider
        self.optional = optional
        self.allow_fixtures = allow_fixtures
        self.database = database
        self.available = True

    def scope(self, identifier):
        if self.tenant_provider is None:
            return identifier
        return hashlib.sha256(self.tenant_provider().encode()).hexdigest() + ":" + identifier

    def tenant(self):
        return self.tenant_provider() if self.tenant_provider else None

    def ensure_constraints(self) -> None:
        """Create Neo4j constraints and indexes required for ingest operations."""

        statements = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
            "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.name)",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Section) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Table) REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (f:Figure) REQUIRE f.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Page) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Paragraph) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:TableCell) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Topic) REQUIRE t.id IS UNIQUE",
        ]
        with self._driver.session(database=self.database) as session:
            for statement in statements:
                session.run(statement)

    def upsert_document(
        self, doc_id: str, title: Optional[str] = None, source: str = "paste",
        *, version: int = 1, content_hash: str | None = None,
    ) -> None:
        """Insert or update a document node and basic metadata."""

        def _tx(tx: Transaction) -> None:
            tx.run(
                "MERGE (d:Document {id: $doc_id}) "
                "SET d.title = $title, d.source = $source, d.version=$version, "
                "d.content_hash=$content_hash, d.link_status='linked', "
                "d.updated_at = timestamp(), d.original_id=$original_id, d.tenant_id=$tenant_id",
                doc_id=self.scope(doc_id),
                title=title,
                source=source,
                original_id=doc_id,
                tenant_id=self.tenant(),
                version=version,
                content_hash=content_hash or doc_id,
            )

        self._execute_write(_tx)

    def upsert_page(self, doc_id: str, page: int, version: int = 1) -> None:
        """Create a tenant-scoped page anchor without storing private content."""
        page_id = f"{doc_id}:page:{page}"

        def _tx(tx: Transaction) -> None:
            tx.run(
                "MATCH (d:Document {id:$doc_id}) MERGE (p:Page {id:$page_id}) "
                "SET p.original_id=$original_id, p.document_id=$original_doc_id, "
                "p.page=$page, p.version=$version, p.tenant_id=$tenant "
                "MERGE (d)-[:HAS_PAGE]->(p)",
                doc_id=self.scope(doc_id), page_id=self.scope(page_id),
                original_id=page_id, original_doc_id=doc_id, page=page,
                version=version, tenant=self.tenant(),
            )

        self._execute_write(_tx)

    def upsert_chunk(
        self, doc_id: str, chunk_id: str, ord: int, text: str, token_count: int
    ) -> None:
        """Insert or update a chunk node with ordering and token metadata."""

        def _tx(tx: Transaction) -> None:
            tx.run(
                "MERGE (c:Chunk {id: $chunk_id}) "
                "SET c.ord = $ord, c.text = $text, c.tokens = $token_count, c.updated_at = timestamp(), c.original_id=$original_id, c.tenant_id=$tenant_id",
                chunk_id=self.scope(chunk_id),
                ord=ord,
                text=text,
                token_count=token_count,
                original_id=chunk_id,
                tenant_id=self.tenant(),
            )

        self._execute_write(_tx)

    def upsert_reference_object(self, doc_id: str, item: dict) -> None:
        """Persist bounded reference metadata only; actual content remains local."""
        label = item.get("object_type", "Chunk")
        if label not in {"Chunk", "Table", "Figure", "Section", "Paragraph", "TableCell"}:
            raise ValueError("Unsupported graph object type")
        description = re.sub(r"\s+", " ", str(item.get("description") or "")).strip()[:160]

        def _tx(tx: Transaction) -> None:
            tx.run(
                f"MERGE (n:{label} {{id: $id}}) "
                "SET n.original_id=$original_id, n.document_id=$document_id, "
                "n.page=$page, n.ordinal=$ordinal, n.description=$description, "
                "n.document_hash=$document_hash, n.content_hash=$content_hash, "
                "n.version=$version, n.source_provenance=$source_provenance, "
                "n.link_status=$link_status, n.tenant_id=$tenant_id",
                id=self.scope(item["object_id"]),
                original_id=item["object_id"],
                document_id=doc_id,
                page=item.get("page"),
                ordinal=item.get("position"),
                description=description,
                document_hash=doc_id,
                tenant_id=self.tenant(),
                content_hash=item.get("content_hash"),
                version=item.get("version", 1),
                source_provenance=item.get("source_provenance"),
                link_status=item.get("link_status", "linked"),
            )
            tx.run(
                f"MATCH (p:Page {{id:$page_id}}), (n:{label} {{id:$id}}) "
                f"MERGE (p)-[:HAS_{label.upper()}]->(n)",
                page_id=self.scope(f"{doc_id}:page:{item.get('page')}"),
                id=self.scope(item["object_id"]),
            )

        self._execute_write(_tx)

    def fetch_structured_references(
        self,
        concepts: Sequence[str],
        limit: int = 20,
        document_ids: Sequence[str] | None = None,
        object_types: Sequence[str] | None = None,
        relationships: Sequence[str] | None = None,
    ):
        """Return tenant-scoped references and descriptions, never document content."""
        terms = [term.casefold() for term in concepts if term.strip()][:10]
        if not terms:
            return []
        allowed_relationships = {
            "HAS_SECTION",
            "HAS_CHUNK",
            "HAS_TABLE",
            "HAS_FIGURE",
            "HAS_PARAGRAPH",
            "HAS_TABLECELL",
            "HAS_CELL",
            "HAS_OBJECT",
            "REFERENCES_TABLE",
            "REFERENCES_FIGURE",
            "MENTIONS",
            "RELATED_TO",
        }
        selected_relationships = [
            relation
            for relation in (relationships or allowed_relationships)
            if relation in allowed_relationships
        ]
        selected_types = [
            label
            for label in (object_types or ["Chunk", "Table", "Figure", "Section", "Paragraph", "TableCell"])
            if label in {"Chunk", "Table", "Figure", "Section", "Paragraph", "TableCell"}
        ]

        def _tx(tx: Transaction):
            result = tx.run(
                "MATCH (d:Document)-[:HAS_PAGE]->(:Page)-[]->(seed) "
                "WHERE d.tenant_id=$tenant AND seed.tenant_id=$tenant "
                "AND (size($document_ids)=0 OR d.original_id IN $document_ids) "
                "AND any(term IN $terms WHERE toLower(coalesce(seed.description,'')) CONTAINS term "
                "OR toLower(coalesce(d.title,'')) CONTAINS term) "
                "MATCH path=(seed)-[*0..2]-(n) "
                "WHERE n.tenant_id=$tenant AND any(label IN labels(n) WHERE label IN $object_types) "
                "AND all(r IN relationships(path) WHERE type(r) IN $relationships) "
                "RETURN DISTINCT coalesce(n.original_id,n.id) AS object_id, labels(n)[0] AS object_type, "
                "n.document_id AS document_id, n.page AS page, n.description AS description "
                "LIMIT $limit",
                tenant=self.tenant(),
                terms=terms,
                document_ids=list(document_ids or []),
                object_types=selected_types,
                relationships=selected_relationships,
                limit=min(max(limit, 1), 20),
            )
            return [dict(record) for record in result]

        return self._execute_read(_tx) or []

    def link_reference_objects(self, source_id: str, target_id: str, relationship: str):
        allowed = {
            "HAS_SECTION",
            "HAS_CHUNK",
            "HAS_TABLE",
            "HAS_FIGURE",
            "HAS_PARAGRAPH",
            "HAS_TABLECELL",
            "HAS_CELL",
            "REFERENCES_TABLE",
            "REFERENCES_FIGURE",
            "MENTIONS",
            "RELATED_TO",
        }
        if relationship not in allowed:
            raise ValueError("Unsupported structured relationship")

        def _tx(tx: Transaction):
            tx.run(
                "MATCH (a {id:$source}), (b {id:$target}) "
                f"WHERE a.tenant_id=$tenant AND b.tenant_id=$tenant MERGE (a)-[:{relationship}]->(b)",
                source=self.scope(source_id),
                target=self.scope(target_id),
                tenant=self.tenant(),
            )

        self._execute_write(_tx)

    def link_doc_chunk(self, doc_id: str, chunk_id: str) -> None:
        """Ensure the relationship between a document and one of its chunks exists."""

        def _tx(tx: Transaction) -> None:
            tx.run(
                "MATCH (d:Document {id: $doc_id}), (c:Chunk {id: $chunk_id}) "
                "MERGE (d)-[:HAS_CHUNK]->(c)",
                doc_id=self.scope(doc_id),
                chunk_id=self.scope(chunk_id),
            )

        self._execute_write(_tx)

    def upsert_entity(self, name: str, type: str = "TERM") -> str:
        """Insert or update an entity node, returning its canonical identifier."""

        def _tx(tx: Transaction) -> str:
            result = tx.run(
                "MERGE (e:Entity {id: $entity_id}) "
                "SET e.name = $name, e.type = $type, e.updated_at = timestamp(), e.tenant_id=$tenant_id "
                "RETURN e.id AS id",
                name=name,
                type=type,
                entity_id=self.scope(name.lower()),
                tenant_id=self.tenant(),
            )
            result.single()
            return name.lower()

        result = self._execute_write(_tx)
        if isinstance(result, str):
            return result
        return name.lower()

    def link_chunk_entity(self, chunk_id: str, entity_id: str, rel: str = "ABOUT") -> None:
        """Create a relationship between a chunk and an entity."""

        rel_type = _safe_rel(rel)

        def _tx(tx: Transaction) -> None:
            tx.run(
                f"MATCH (c:Chunk {{id: $chunk_id}}), (e:Entity {{id: $entity_id}}) "
                f"MERGE (c)-[:{rel_type}]->(e)",
                chunk_id=self.scope(chunk_id),
                entity_id=self.scope(entity_id),
            )

        self._execute_write(_tx)

    def get_entity_degrees(self, names: Iterable[str]) -> Dict[str, int]:
        """Return a mapping of entity id to degree counts for the requested names."""

        name_list = list(names)
        if not name_list:
            return {}

        lower_names = [self.scope(name.lower()) for name in name_list]

        def _tx(tx: Transaction) -> Dict[str, int]:
            result = tx.run(
                "MATCH (e:Entity) WHERE e.id IN $ids "
                "OPTIONAL MATCH (e)--(n) "
                "RETURN e.id AS id, count(n) AS degree",
                ids=lower_names,
            )
            return {record["id"]: record["degree"] for record in result}

        degrees = self._execute_read(_tx)
        return {name: (degrees or {}).get(self.scope(name.lower()), 0) for name in name_list}

    def fetch_chunks_for_entities(self, names: Sequence[str], limit: int) -> List[ChunkRecord]:
        """Fetch chunks connected to any of the provided entity names."""

        if not names:
            return []

        lower_names = [self.scope(name.lower()) for name in names]

        def _tx(tx: Transaction) -> List[ChunkRecord]:
            result = tx.run(
                "MATCH (e:Entity) WHERE e.id IN $ids "
                "MATCH (e)<-[:ABOUT]-(c:Chunk)<-[:HAS_CHUNK]-(d:Document) "
                "RETURN coalesce(c.original_id,c.id) AS chunk_id, c.text AS text, c.ord AS ord, coalesce(d.original_id,d.id) AS doc_id, d.title AS title",
                ids=lower_names,
            )
            records: List[ChunkRecord] = []
            for record in result:
                from backend.app.services.knowledge_policy import is_test_knowledge

                if not self.allow_fixtures and is_test_knowledge({"title": record.get("title")}):
                    continue
                records.append(
                    {
                        "id": record["chunk_id"],
                        "text": record["text"],
                        "metadata": {
                            "doc_id": record["doc_id"],
                            "title": record.get("title"),
                            "ord": record.get("ord"),
                        },
                        "score": 0.0,
                    }
                )
            return records

        records = self._execute_read(_tx)
        return (records or [])[:limit]

    def upsert_relationships(self, relations):
        """Store only supplied relationships with explicit source provenance."""
        for item in relations:
            rel = _safe_rel(item["predicate"])
            if not self.allow_fixtures and item.get("source_system") == "local_fixture":
                continue

            def write(tx):
                tx.run(
                    f"MERGE (a:Entity {{id: $subject_id}}) SET a.name=$subject, a.tenant_id=$tenant "
                    f"MERGE (b:Entity {{id: $object_id}}) SET b.name=$object, b.tenant_id=$tenant "
                    f"MERGE (a)-[r:{rel}]->(b) "
                    "SET r.source_system=$source_system, r.observed_at=datetime() ",
                    subject=item["subject"],
                    object=item["object"],
                    subject_id=self.scope(item["subject"].lower()),
                    object_id=self.scope(item["object"].lower()),
                    tenant=self.tenant(),
                    source_system=item.get("source_system", "knowledge_graph"),
                )

            self._execute_write(write)

    def search_relationships(self, entities, limit=12):
        """Bounded two-hop relationship discovery, with parameterized terms."""
        terms = [s.casefold() for s in entities if s.strip()][:10]
        if not terms:
            return []

        def read(tx):
            rows = tx.run(
                "MATCH (seed:Entity) WHERE any(term IN $terms WHERE toLower(seed.name) CONTAINS term) "
                "AND ($tenant IS NULL OR seed.tenant_id=$tenant) "
                "MATCH path=(seed)-[*1..2]-(neighbor:Entity) "
                "UNWIND relationships(path) AS r "
                "WITH r WHERE ($tenant IS NULL OR (startNode(r).tenant_id=$tenant AND endNode(r).tenant_id=$tenant)) "
                'AND type(r) IN ["WORKS_AT","HAS_SITE","USES","AFFECTS","APPLIES_TO","INVOLVES","ASSOCIATED_WITH"] '
                "AND ($allow_fixtures OR coalesce(r.source_system, '') <> 'local_fixture') "
                "RETURN DISTINCT startNode(r).name AS subject, type(r) AS predicate, "
                "endNode(r).name AS object, r.source_system AS source_system, "
                "toString(r.observed_at) AS observed_at LIMIT $limit",
                terms=terms,
                limit=min(max(limit, 1), 50),
                tenant=self.tenant(),
                allow_fixtures=self.allow_fixtures,
            )
            return [dict(row) for row in rows]

        return self._execute_read(read) or []

    def ping(self) -> bool:
        """Return True when the database responds to a trivial read."""

        def _tx(tx: Transaction) -> bool:
            tx.run("RETURN 1 AS ok")
            return True

        try:
            return bool(self._execute_read(_tx))
        except Exception:  # pragma: no cover - depends on runtime connectivity
            return False

    def _execute_write(self, func: Callable[[Transaction], T]) -> T:
        try:
            if hasattr(self._driver, "execute_write"):
                return self._driver.execute_write(func)
            with self._driver.session(database=self.database) as session:
                return session.execute_write(func)
        except Exception:
            if not self.optional:
                raise
            self.available = False
            logging.getLogger(__name__).warning(
                "Aura graph write unavailable; local knowledge preserved"
            )
            return None

    def _execute_read(self, func: Callable[[Transaction], T]) -> T:
        try:
            if hasattr(self._driver, "execute_read"):
                return self._driver.execute_read(func)
            with self._driver.session(database=self.database) as session:
                return session.execute_read(func)
        except Exception:
            if not self.optional:
                raise
            self.available = False
            logging.getLogger(__name__).warning(
                "Aura graph read unavailable; using local vector evidence"
            )
            return None


class UnavailableGraphRepository:
    """Honest vector-only fallback; never supplies synthetic graph evidence."""

    available = False

    def ensure_constraints(self):
        pass

    def upsert_document(self, *args, **kwargs):
        pass

    def upsert_chunk(self, *args, **kwargs):
        pass

    def upsert_page(self, *args, **kwargs):
        pass

    def link_doc_chunk(self, *args, **kwargs):
        pass

    def upsert_entity(self, name, **kwargs):
        return name.casefold()

    def link_chunk_entity(self, *args, **kwargs):
        pass

    def upsert_relationships(self, *args, **kwargs):
        pass

    def get_entity_degrees(self, names):
        return {name: 0 for name in names}

    def fetch_chunks_for_entities(self, names, limit):
        return []

    def fetch_structured_references(self, concepts, limit=20, **kwargs):
        return []

    def upsert_reference_object(self, *args, **kwargs):
        pass

    def link_reference_objects(self, *args, **kwargs):
        pass

    def search_relationships(self, entities, limit=12):
        return []

    def ping(self):
        return False


class InMemoryGraphRepository:
    """Minimal in-memory substitute implementing the GraphRepository interface."""

    def __init__(self) -> None:
        """Initialise empty dictionaries for documents, chunks, and entities."""
        self.documents: MutableMapping[str, Dict[str, Optional[str]]] = {}
        self.chunks: MutableMapping[str, Dict[str, Any]] = {}
        self.entity_links: MutableMapping[str, Dict[str, Any]] = {}
        self.references: MutableMapping[str, Dict[str, Any]] = {}

    def ensure_constraints(self) -> None:  # pragma: no cover - no-op for in-memory variant
        """In-memory store has no constraints to create."""

    def upsert_document(
        self, doc_id: str, title: Optional[str] = None, source: str = "paste", **metadata
    ) -> None:
        """Store document metadata in-memory."""

        self.documents[doc_id] = {"title": title, "source": source}

    def upsert_page(self, doc_id: str, page: int, version: int = 1) -> None:
        """Pages are implicit in the lightweight test repository."""

    def upsert_chunk(
        self, doc_id: str, chunk_id: str, ord: int, text: str, token_count: int
    ) -> None:
        """Persist chunk metadata in-memory for testing."""

        self.chunks[chunk_id] = {
            "doc_id": doc_id,
            "text": text,
            "ord": ord,
            "tokens": token_count,
        }

    def upsert_reference_object(self, doc_id: str, item: dict) -> None:
        self.references[item["object_id"]] = {
            "object_id": item["object_id"],
            "object_type": item["object_type"],
            "document_id": doc_id,
            "page": item.get("page"),
            "description": str(item.get("description") or "")[:160],
        }

    def fetch_structured_references(
        self,
        concepts: Sequence[str],
        limit: int = 20,
        document_ids=None,
        object_types=None,
        relationships=None,
    ):
        terms = [term.casefold() for term in concepts]
        return [
            dict(item)
            for item in self.references.values()
            if (
                any(term in item["description"].casefold() for term in terms)
                or any(
                    term
                    in str(
                        self.documents.get(item["document_id"], {}).get("title") or ""
                    ).casefold()
                    for term in terms
                )
            )
            and (not document_ids or item["document_id"] in document_ids)
            and (not object_types or item["object_type"] in object_types)
        ][: min(max(limit, 1), 20)]

    def link_reference_objects(self, source_id: str, target_id: str, relationship: str):
        if source_id not in self.references or target_id not in self.references:
            raise ValueError("Unknown reference object")

    def link_doc_chunk(self, doc_id: str, chunk_id: str) -> None:  # pragma: no cover - no-op
        """Document/chunk relationships are implicit in the in-memory structure."""

    def upsert_entity(self, name: str, type: str = "TERM") -> str:
        """Store entity metadata and return its normalized id."""

        entity_id = name.lower()
        if entity_id not in self.entity_links:
            self.entity_links[entity_id] = {"name": name, "type": type, "chunks": set()}
        return entity_id

    def link_chunk_entity(self, chunk_id: str, entity_id: str, rel: str = "ABOUT") -> None:
        """Record a chunk→entity relationship in-memory."""

        info = self.entity_links.setdefault(
            entity_id, {"name": entity_id, "type": "TERM", "chunks": set()}
        )
        chunks: Set[str] = info.setdefault("chunks", set())  # type: ignore[assignment]
        chunks.add(chunk_id)

    def get_entity_degrees(self, names: Iterable[str]) -> Dict[str, int]:
        """Return entity linkage counts for the supplied names."""

        degrees: Dict[str, int] = {}
        for name in names:
            entity_id = name.lower()
            chunks = self.entity_links.get(entity_id, {"chunks": set()}).get("chunks", set())
            degrees[name] = len(chunks)
        return degrees

    def fetch_chunks_for_entities(self, names: Sequence[str], limit: int) -> List[ChunkRecord]:
        """Return stored chunks associated with the requested entities."""

        collected: List[ChunkRecord] = []
        for name in names:
            entity_id = name.lower()
            for chunk_id in self.entity_links.get(entity_id, {"chunks": set()}).get(
                "chunks", set()
            ):
                chunk = self.chunks.get(chunk_id)
                if not chunk:
                    continue
                metadata = {
                    "doc_id": chunk["doc_id"],
                    "title": self.documents.get(chunk["doc_id"], {}).get("title"),
                    "ord": chunk.get("ord"),
                }
                collected.append(
                    {"id": chunk_id, "text": chunk["text"], "metadata": metadata, "score": 0.0}
                )
        return collected[:limit]

    def ping(self) -> bool:
        """In-memory store is always available."""

        return True


def _safe_rel(rel: str) -> str:
    """Return a Neo4j relationship label comprised of safe characters only."""

    candidate = (rel or "ABOUT").upper()
    if not re.match(r"^[A-Z_][A-Z0-9_]*$", candidate):
        return "ABOUT"
    return candidate
