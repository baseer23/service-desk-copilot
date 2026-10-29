from __future__ import annotations

import json
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


# Reference-only structural labels; their text stays in local storage.
REFERENCE_LABELS = {
    "Chunk", "Table", "Figure", "Section", "Paragraph", "TableCell", "FormField", "SelectionMark",
}
REFERENCE_RELATIONSHIPS = {
    "HAS_SECTION", "HAS_CHUNK", "HAS_TABLE", "HAS_FIGURE", "HAS_PARAGRAPH", "HAS_TABLECELL",
    "HAS_CELL", "HAS_FORMFIELD", "HAS_SELECTIONMARK", "REFERENCES_TABLE", "REFERENCES_FIGURE",
    "MENTIONS", "RELATED_TO",
}
WRITE_BATCH_ROWS = 500


def _batches(rows):
    for start in range(0, len(rows), WRITE_BATCH_ROWS):
        yield rows[start:start + WRITE_BATCH_ROWS]


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
            "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.normalized_name)",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:GraphClaim) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (m:GraphMention) REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Section) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Table) REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (f:Figure) REQUIRE f.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Page) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Paragraph) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:TableCell) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (f:FormField) REQUIRE f.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (m:SelectionMark) REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Topic) REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (f:SemanticFact) REQUIRE f.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:ConversationSource) REQUIRE s.id IS UNIQUE",
        ]
        with self._driver.session(database=self.database) as session:
            for statement in statements:
                session.run(statement)

    def upsert_document(
        self, doc_id: str, title: Optional[str] = None, source: str = "paste",
        *, version: int = 1, content_hash: str | None = None,
        source_type: str | None = None, source_url: str | None = None,
        canonical_url: str | None = None,
    ) -> None:
        """Insert or update a document node, its metadata and source provenance."""

        def _tx(tx: Transaction) -> None:
            tx.run(
                "MERGE (d:Document {id: $doc_id}) "
                "SET d.title = $title, d.source = $source, d.version=$version, "
                "d.content_hash=$content_hash, d.link_status='linked', "
                "d.updated_at = timestamp(), d.original_id=$original_id, d.tenant_id=$tenant_id, "
                "d.source_type=$source_type, d.source_url=$source_url, "
                "d.canonical_url=$canonical_url",
                doc_id=self.scope(doc_id),
                title=title,
                source=source,
                original_id=doc_id,
                tenant_id=self.tenant(),
                version=version,
                content_hash=content_hash or doc_id,
                source_type=source_type,
                source_url=source_url or None,
                canonical_url=canonical_url or None,
            )

        self._execute_write(_tx)

    def upsert_page(self, doc_id: str, page: int, version: int = 1) -> None:
        """Create a tenant-scoped page anchor without storing private content."""
        self.upsert_pages(doc_id, [page], version)

    def upsert_pages(self, doc_id: str, pages, version: int = 1) -> None:
        """Create many page anchors in one transaction (one round trip per batch)."""
        rows = [
            {"page_id": self.scope(f"{doc_id}:page:{page}"),
             "original_id": f"{doc_id}:page:{page}", "page": page}
            for page in pages
        ]
        if not rows:
            return

        def _tx(tx: Transaction) -> None:
            for batch in _batches(rows):
                tx.run(
                    "MATCH (d:Document {id:$doc_id}) UNWIND $rows AS row "
                    "MERGE (p:Page {id:row.page_id}) "
                    "SET p.original_id=row.original_id, p.document_id=$original_doc_id, "
                    "p.page=row.page, p.version=$version, p.tenant_id=$tenant "
                    "MERGE (d)-[:HAS_PAGE]->(p)",
                    rows=batch, doc_id=self.scope(doc_id), original_doc_id=doc_id,
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
        self.upsert_reference_objects(doc_id, [item])

    def upsert_reference_objects(self, doc_id: str, items) -> None:
        """Persist many reference objects with one UNWIND per label and batch.

        A per-object transaction costs several Aura round trips, which made
        linking a large form take minutes.
        """
        grouped: dict[str, list[dict]] = {}
        for item in items:
            label = item.get("object_type", "Chunk")
            if label not in REFERENCE_LABELS:
                raise ValueError("Unsupported graph object type")
            grouped.setdefault(label, []).append(self._reference_row(doc_id, item))
        if not grouped:
            return

        def _tx(tx: Transaction) -> None:
            for label, rows in grouped.items():
                for batch in _batches(rows):
                    tx.run(
                        f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) "
                        "SET n.original_id=row.original_id, n.document_id=row.document_id, "
                        "n.page=row.page, n.ordinal=row.ordinal, n.description=row.description, "
                        "n.document_hash=row.document_hash, n.content_hash=row.content_hash, "
                        "n.version=row.version, n.source_provenance=row.source_provenance, "
                        "n.link_status=row.link_status, n.tenant_id=row.tenant_id, "
                        "n.source_element_id=row.source_element_id, n.azure_role=row.azure_role, "
                        "n.span_offset=row.span_offset, n.span_length=row.span_length, "
                        "n.table_row=row.table_row, n.table_column=row.table_column, "
                        "n.role=row.role, n.state_flag=row.state_flag, "
                        "n.confidence=row.confidence, "
                        "n.bounding_regions_json=row.bounding_regions_json",
                        rows=batch,
                    )
                    if label == "SelectionMark":
                        continue  # reached through its FormField, not directly from the Page
                    tx.run(
                        f"UNWIND $rows AS row MATCH (p:Page {{id:row.page_id}}) "
                        f"MATCH (n:{label} {{id:row.id}}) MERGE (p)-[:HAS_{label.upper()}]->(n)",
                        rows=batch,
                    )

        self._execute_write(_tx)

    def _reference_row(self, doc_id: str, item: dict) -> dict:
        description = re.sub(r"\s+", " ", str(item.get("description") or "")).strip()[:160]
        return {
            "id": self.scope(item["object_id"]),
            "page_id": self.scope(f"{doc_id}:page:{item.get('page')}"),
            "original_id": item["object_id"],
            "document_id": doc_id,
            "page": item.get("page"),
            "ordinal": item.get("position"),
            "description": description,
            "document_hash": doc_id,
            "tenant_id": self.tenant(),
            "content_hash": item.get("content_hash"),
            "version": item.get("version", 1),
            "source_provenance": item.get("source_provenance"),
            "link_status": item.get("link_status", "linked"),
            "source_element_id": item.get("source_element_id"),
            "azure_role": item.get("azure_role"),
            "span_offset": item.get("span_offset"),
            "span_length": item.get("span_length"),
            "table_row": item.get("table_row"),
            "table_column": item.get("table_column"),
            # Form references carry a role, a state flag and a confidence,
            # never the question's answer text.
            "role": item.get("role"),
            "state_flag": item.get("state_flag"),
            "confidence": item.get("confidence"),
            "bounding_regions_json": json.dumps(
                item.get("bounding_regions", [])[:8], separators=(",", ":")
            )[:4000],
        }

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
            "HAS_FORMFIELD",
            "HAS_SELECTIONMARK",
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
            for label in (object_types or sorted(REFERENCE_LABELS))
            if label in REFERENCE_LABELS
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
                "WITH DISTINCT n,d, reduce(score=0, term IN $terms | score + "
                "CASE WHEN toLower(coalesce(n.description,'')) CONTAINS term THEN 2 ELSE 0 END + "
                "CASE WHEN toLower(coalesce(d.title,'')) CONTAINS term THEN 1 ELSE 0 END) AS relevance "
                "RETURN coalesce(n.original_id,n.id) AS object_id, labels(n)[0] AS object_type, "
                "coalesce(n.document_id,d.original_id) AS document_id, n.page AS page, "
                "n.description AS description ORDER BY relevance DESC, n.page ASC "
                "LIMIT $limit",
                tenant=self.tenant(),
                terms=terms,
                document_ids=list(document_ids or []),
                object_types=selected_types,
                relationships=selected_relationships,
                limit=min(max(limit, 1), 200),
            )
            return [dict(record) for record in result]

        return self._execute_read(_tx) or []

    def link_website_document(self, doc_id: str, origin: str, canonical_url: str, outlinks):
        """Attach a crawled page to its Website and to linked pages, in one transaction.

        LINKS_TO is written in both directions against Documents already in the
        graph (matched by canonical_url), so pages can be linked in any order: a
        link to a page not yet saved is written when that page arrives. Counts
        are relationships actually created.
        """
        outlinks = [str(url) for url in list(dict.fromkeys(outlinks or []))[:500]]

        def _tx(tx: Transaction):
            attached = tx.run(
                "MATCH (d:Document {id:$doc}) WHERE d.tenant_id=$tenant "
                "SET d.outlinks=$outlinks "
                "MERGE (w:Website {id:$website}) ON CREATE SET w.created_at=timestamp() "
                "SET w.origin=$origin, w.original_id=$origin, w.tenant_id=$tenant "
                "MERGE (w)-[:HAS_DOCUMENT]->(d) RETURN count(d) AS attached",
                doc=self.scope(doc_id), tenant=self.tenant(), outlinks=outlinks,
                website=self.scope("website:" + origin), origin=origin,
            ).single()
            if not attached or not attached["attached"]:
                # The page Document is not in the graph yet: nothing was written.
                return {"outbound": 0, "inbound": 0, "document_found": False}
            outbound = tx.run(
                "MATCH (d:Document {id:$doc}) WHERE d.tenant_id=$tenant "
                "UNWIND $outlinks AS url "
                "MATCH (t:Document {canonical_url:url}) WHERE t.tenant_id=$tenant AND t <> d "
                "MERGE (d)-[:LINKS_TO]->(t)",
                doc=self.scope(doc_id), tenant=self.tenant(), outlinks=outlinks,
            ).consume().counters.relationships_created
            inbound = tx.run(
                "MATCH (d:Document {id:$doc}) WHERE d.tenant_id=$tenant "
                "MATCH (s:Document) WHERE s.tenant_id=$tenant AND s <> d "
                "AND $canonical IN coalesce(s.outlinks, []) "
                "MERGE (s)-[:LINKS_TO]->(d)",
                doc=self.scope(doc_id), tenant=self.tenant(), canonical=canonical_url,
            ).consume().counters.relationships_created
            # Counters report relationships actually created, not existing ones re-matched.
            return {"outbound": outbound, "inbound": inbound, "document_found": True}

        return self._execute_write(_tx) or {"outbound": 0, "inbound": 0, "document_found": False}

    def link_reference_objects(
        self, source_id: str, target_id: str, relationship: str,
        source_label: str | None = None, target_label: str | None = None,
    ):
        self.link_reference_objects_many(
            [(source_id, target_id, relationship, source_label, target_label)]
        )

    def link_reference_objects_many(self, links) -> None:
        """Link reference objects in batches of (source, target, relationship[, labels]).

        Known labels let the match use the per-label id constraints instead
        of scanning every node in the database for each link.
        """
        grouped: dict[tuple, list[dict]] = {}
        for source_id, target_id, relationship, *labels in links:
            source_label, target_label = [*labels, None, None][:2]
            if relationship not in REFERENCE_RELATIONSHIPS:
                raise ValueError("Unsupported structured relationship")
            if {source_label, target_label} - REFERENCE_LABELS - {None}:
                raise ValueError("Unsupported graph object type")
            grouped.setdefault((relationship, source_label, target_label), []).append(
                {"source": self.scope(source_id), "target": self.scope(target_id)}
            )
        if not grouped:
            return

        def _tx(tx: Transaction):
            for (relationship, source_label, target_label), rows in grouped.items():
                source = f":{source_label}" if source_label else ""
                target = f":{target_label}" if target_label else ""
                for batch in _batches(rows):
                    tx.run(
                        f"UNWIND $rows AS row MATCH (a{source} {{id:row.source}}) "
                        f"MATCH (b{target} {{id:row.target}}) "
                        "WHERE a.tenant_id=$tenant AND b.tenant_id=$tenant "
                        f"MERGE (a)-[:{relationship}]->(b)",
                        rows=batch, tenant=self.tenant(),
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
                'AND (type(r) IN ["WORKS_AT","HAS_SITE","USES","AFFECTS","APPLIES_TO","INVOLVES","ASSOCIATED_WITH"] OR type(r)="RELATED_TO") '
                "AND ($allow_fixtures OR coalesce(r.source_system, '') <> 'local_fixture') "
                "RETURN DISTINCT startNode(r).name AS subject, coalesce(r.predicate,type(r)) AS predicate, "
                "endNode(r).name AS object, r.source_system AS source_system, "
                "toString(r.observed_at) AS observed_at LIMIT $limit",
                terms=terms,
                limit=min(max(limit, 1), 50),
                tenant=self.tenant(),
                allow_fixtures=self.allow_fixtures,
            )
            return [dict(row) for row in rows]

        return self._execute_read(read) or []

    def find_entity_candidates(self, name, entity_type=None, aliases=None, identifiers=None, limit=20):
        """Find compact candidate identities without mutating the graph."""
        terms = list(dict.fromkeys(v.casefold() for v in [name, *(aliases or [])] if v.strip()))[:20]
        identifier_values = [str(v).casefold() for v in (identifiers or {}).values() if str(v).strip()][:20]

        def read(tx):
            rows = tx.run(
                "MATCH (e:Entity) WHERE e.tenant_id=$tenant AND ("
                "toLower(coalesce(e.normalized_name,e.name,'')) IN $terms OR "
                "any(a IN coalesce(e.aliases,[]) WHERE toLower(a) IN $terms) OR "
                "any(v IN $identifiers WHERE toLower(coalesce(e.identifiers_json,'')) CONTAINS v)) "
                "RETURN coalesce(e.original_id,toLower(e.name)) AS id,e.name AS name,"
                "coalesce(e.kind,e.type) AS kind,e.aliases AS aliases,e.description AS description "
                "ORDER BY CASE WHEN toLower(coalesce(e.normalized_name,e.name,''))=$name THEN 0 ELSE 1 END "
                "LIMIT $limit",
                tenant=self.tenant(), terms=terms, identifiers=identifier_values,
                name=name.casefold(), limit=min(max(limit, 1), 20),
            )
            return [dict(row) for row in rows]
        return self._execute_read(read) or []

    def get_entity_neighborhood(self, entity_id, max_hops=2, limit=30):
        hops = 1 if max_hops <= 1 else 2
        query = (
            f"MATCH (seed:Entity {{id:$id}}) WHERE seed.tenant_id=$tenant "
            f"OPTIONAL MATCH path=(seed)-[*1..{hops}]-(other:Entity) "
            "WHERE other.tenant_id=$tenant UNWIND relationships(path) AS r "
            "RETURN DISTINCT coalesce(startNode(r).original_id,toLower(startNode(r).name)) AS source_id,"
            "startNode(r).name AS source,coalesce(r.predicate,type(r)) AS predicate,"
            "coalesce(endNode(r).original_id,toLower(endNode(r).name)) AS target_id,endNode(r).name AS target LIMIT $limit"
        )
        def read(tx):
            return [dict(row) for row in tx.run(query, id=self.scope(entity_id), tenant=self.tenant(), limit=min(max(limit, 1), 30))]
        return {"entity_id": entity_id, "relationships": self._execute_read(read) or []}

    def find_related_documents(self, entity_id, limit=20):
        def read(tx):
            rows = tx.run(
                "MATCH (e:Entity {id:$id}) WHERE e.tenant_id=$tenant "
                "MATCH (e)<-[:REFERS_TO]-(m:GraphMention)-[:MENTIONED_IN]->(d:Document) "
                "OPTIONAL MATCH (m)-[:SUPPORTED_BY]->(o) "
                "RETURN coalesce(d.original_id,d.id) AS document_id,d.title AS title,"
                "d.summary AS summary,collect(DISTINCT coalesce(o.original_id,o.id))[0..50] AS object_ids LIMIT $limit",
                id=self.scope(entity_id), tenant=self.tenant(), limit=min(max(limit, 1), 20),
            )
            return [dict(row) for row in rows]
        return self._execute_read(read) or []

    def existing_entity_ids(self, entity_ids):
        logical = list(dict.fromkeys(entity_ids))[:100]
        def read(tx):
            rows = tx.run("MATCH (e:Entity) WHERE e.tenant_id=$tenant AND e.id IN $ids "
                          "RETURN coalesce(e.original_id,toLower(e.name)) AS id",
                          tenant=self.tenant(), ids=[self.scope(value) for value in logical])
            return [row["id"] for row in rows]
        return set(self._execute_read(read) or [])

    def update_document_semantics(self, document_id, *, document_type, summary, status):
        def write(tx):
            tx.run("MATCH (d:Document {id:$id}) WHERE d.tenant_id=$tenant "
                   "SET d.document_type=$type,d.summary=$summary,d.semantic_graph_status=$status,d.semantic_updated_at=timestamp()",
                   id=self.scope(document_id), tenant=self.tenant(), type=document_type[:160], summary=summary[:1200], status=status)
        self._execute_write(write)

    def upsert_knowledge_entities(self, entities):
        for entity in entities:
            def write(tx, entity=entity):
                tx.run("MERGE (e:Entity {id:$id}) ON CREATE SET e.created_at=timestamp() "
                       "SET e.original_id=$original_id,e.tenant_id=$tenant,e.name=$name,e.normalized_name=$normalized,"
                       "e.kind=$kind,e.aliases=$aliases,e.identifiers_json=$identifiers,e.description=$description,e.updated_at=timestamp()",
                       id=self.scope(entity["id"]), original_id=entity["id"], tenant=self.tenant(),
                       name=entity["name"][:240], normalized=entity["name"].casefold()[:240], kind=entity["kind"][:100],
                       aliases=[str(v)[:240] for v in entity.get("aliases", [])[:20]],
                       identifiers=json.dumps(entity.get("identifiers", {}), sort_keys=True)[:4000],
                       description=str(entity.get("description", ""))[:1200])
            self._execute_write(write)
        return {"upserted": len(entities)}

    def upsert_knowledge_relationships(self, relationships):
        """Write claims; the count is rows actually matched, never proposals."""
        written = 0
        for item in relationships:
            evidence = list(dict.fromkeys(item.get("evidence_object_ids", [])))[:50]
            claim_id = "claim_" + hashlib.sha256(json.dumps({
                "s": item["source_id"], "p": item["predicate"], "o": item["target_id"],
                "d": item["document_id"], "e": evidence}, sort_keys=True).encode()).hexdigest()[:32]
            def write_scoped(tx, item=item, evidence=evidence, claim_id=claim_id):
                result = tx.run("MATCH (a:Entity {id:$source}),(b:Entity {id:$target}),(d:Document {id:$document}) "
                       "WHERE a.tenant_id=$tenant AND b.tenant_id=$tenant AND d.tenant_id=$tenant "
                       "MERGE (a)-[r:RELATED_TO {predicate:$predicate}]->(b) ON CREATE SET r.created_at=timestamp() SET r.updated_at=timestamp() "
                       "MERGE (c:GraphClaim {id:$claim}) SET c.original_id=$claim_original,c.tenant_id=$tenant,c.predicate=$predicate,"
                       "c.confidence=$confidence,c.is_explicit=$explicit,c.description=$description,c.observed_at=$observed_at,c.status='documented' "
                       "MERGE (c)-[:SUBJECT]->(a) MERGE (c)-[:OBJECT]->(b) MERGE (c)-[:DOCUMENTED_IN]->(d) "
                       "RETURN count(c) AS written",
                       source=self.scope(item["source_id"]), target=self.scope(item["target_id"]), document=self.scope(item["document_id"]),
                       tenant=self.tenant(), predicate=item["predicate"][:64], claim=self.scope(claim_id), claim_original=claim_id,
                       confidence=float(item.get("confidence", 0)), explicit=bool(item.get("is_explicit", True)),
                       description=str(item.get("description", ""))[:1200], observed_at=item.get("observed_at"))
                record = result.single()
                if evidence:
                    tx.run("MATCH (c:GraphClaim {id:$claim}) UNWIND $evidence AS oid "
                           "MATCH (o {id:oid}) WHERE c.tenant_id=$tenant AND o.tenant_id=$tenant "
                           "MERGE (c)-[:SUPPORTED_BY]->(o)", claim=self.scope(claim_id),
                           evidence=[self.scope(v) for v in evidence], tenant=self.tenant())
                return record["written"] if record else 0
            written += self._execute_write(write_scoped) or 0
        return {"upserted": written, "proposed": len(relationships)}

    def link_document_mentions(self, document_id, mentions):
        """Link mentions; counts are rows actually matched, never proposals."""
        linked = resolved = 0
        for item in mentions:
            mention_id = "mention_" + hashlib.sha256(f"{document_id}:{item['mention_id']}".encode()).hexdigest()[:32]
            def write(tx, item=item, mention_id=mention_id):
                result = tx.run("MATCH (d:Document {id:$document}) WHERE d.tenant_id=$tenant "
                       "MERGE (m:GraphMention {id:$mention}) SET m.original_id=$mention_original,m.tenant_id=$tenant,"
                       "m.name=$name,m.kind=$kind,m.status=$status MERGE (m)-[:MENTIONED_IN]->(d) "
                       "RETURN count(m) AS linked",
                       document=self.scope(document_id), tenant=self.tenant(), mention=self.scope(mention_id),
                       mention_original=mention_id, name=item["name"][:240], kind=item["kind"][:100], status=item["status"])
                record = result.single()
                evidence = item.get("evidence_object_ids", [])[:50]
                if evidence:
                    tx.run("MATCH (m:GraphMention {id:$mention}) UNWIND $evidence AS oid "
                           "MATCH (o {id:oid}) WHERE m.tenant_id=$tenant AND o.tenant_id=$tenant "
                           "MERGE (m)-[:SUPPORTED_BY]->(o)", mention=self.scope(mention_id),
                           evidence=[self.scope(v) for v in evidence], tenant=self.tenant())
                refers = 0
                if item.get("entity_id"):
                    refers_record = tx.run("MATCH (m:GraphMention {id:$mention}),(e:Entity {id:$entity}) "
                           "WHERE m.tenant_id=$tenant AND e.tenant_id=$tenant MERGE (m)-[:REFERS_TO]->(e) "
                           "RETURN count(e) AS resolved",
                           mention=self.scope(mention_id), entity=self.scope(item["entity_id"]), tenant=self.tenant()).single()
                    refers = refers_record["resolved"] if refers_record else 0
                return (record["linked"] if record else 0), refers
            counts = self._execute_write(write) or (0, 0)
            linked += counts[0]
            resolved += counts[1]
        return {"linked": linked, "resolved": resolved, "proposed": len(mentions)}

    def upsert_semantic_memory(self, batch) -> None:
        """Idempotently publish validated, owner-scoped facts and provenance."""
        allowed_labels = {"User", "Person", "Organization", "Project", "Location", "Technology"}
        tenant = self.tenant()
        for entity in batch.entities:
            if entity.entity_type not in allowed_labels:
                raise ValueError("Unsupported semantic entity type")
            if tenant is not None and entity.tenant_id != tenant:
                raise PermissionError("Semantic entity tenant mismatch")

            def write_entity(tx, entity=entity):
                tx.run(
                    f"MERGE (e:{entity.entity_type} {{id:$id}}) "
                    "SET e.name=$name, e.tenant_id=$tenant, e.owner_id=$owner, e.updated_at=timestamp()",
                    id=self.scope(entity.id), name=entity.name,
                    tenant=entity.tenant_id, owner=entity.owner_id,
                )

            self._execute_write(write_entity)
        for source in batch.sources:
            def write_source(tx, source=source):
                tx.run(
                    "MERGE (s:ConversationSource {id:$id}) "
                    "SET s.source_type=$source_type, s.source_id=$source_id, "
                    "s.content_hash=$content_hash, s.observed_at=$observed_at, "
                    "s.tenant_id=$tenant, s.owner_id=$owner, s.updated_at=timestamp()",
                    id=self.scope(source.id), source_type=source.source_type,
                    source_id=source.source_id, content_hash=source.content_hash,
                    observed_at=source.observed_at, tenant=tenant, owner=source.owner_id,
                )

            self._execute_write(write_source)
        for fact in batch.facts:
            if tenant is not None and fact.tenant_id != tenant:
                raise PermissionError("Semantic fact tenant mismatch")

            def write_fact(tx, fact=fact):
                tx.run(
                    "MATCH (subject {id:$subject_id}) "
                    "WHERE subject.tenant_id=$tenant AND subject.owner_id=$owner "
                    "MERGE (f:SemanticFact {id:$fact_id}) "
                    "SET f.predicate=$predicate, f.category=$category, f.value=$value, f.confidence=$confidence, "
                    "f.status=$status, f.tenant_id=$tenant, f.owner_id=$owner, "
                    "f.original_id=$original_id, f.updated_at=timestamp() "
                    "MERGE (subject)-[:HAS_FACT]->(f) "
                    "WITH f MATCH (s:ConversationSource {id:$source_id}) "
                    "WHERE s.tenant_id=$tenant MERGE (f)-[:SUPPORTED_BY]->(s)",
                    subject_id=self.scope(fact.subject_id), fact_id=self.scope(fact.id),
                    predicate=fact.predicate, category=fact.category,
                    value=fact.value, confidence=fact.confidence,
                    status=fact.status, tenant=fact.tenant_id, owner=fact.owner_id,
                    source_id=self.scope(fact.source_id), original_id=fact.id,
                )
                if fact.supersedes_id:
                    tx.run(
                        "MATCH (new:SemanticFact {id:$new_id}), (old:SemanticFact {id:$old_id}) "
                        "WHERE new.tenant_id=$tenant AND new.owner_id=$owner "
                        "AND old.tenant_id=$tenant AND old.owner_id=$owner "
                        "SET old.status='superseded' MERGE (new)-[:SUPERSEDES]->(old)",
                        new_id=self.scope(fact.id), old_id=self.scope(fact.supersedes_id),
                        tenant=fact.tenant_id, owner=fact.owner_id,
                    )

            self._execute_write(write_fact)
        allowed_relationships = {"WORKS_AT", "MEMBER_OF", "USES", "LOCATED_IN", "RELATED_TO"}
        for relation in batch.relationships:
            if relation.relationship not in allowed_relationships:
                raise ValueError("Unsupported semantic relationship")

            def write_relationship(tx, relation=relation):
                tx.run(
                    f"MATCH (source {{id:$source_id}}), (target {{id:$target_id}}), "
                    "(fact:SemanticFact {id:$fact_id}), (provenance:ConversationSource {id:$provenance_id}) "
                    "WHERE source.tenant_id=$tenant AND source.owner_id=$owner "
                    "AND target.tenant_id=$tenant AND target.owner_id=$owner "
                    f"MERGE (source)-[r:{relation.relationship} {{fact_id:$fact_id}}]->(target) "
                    "SET r.confidence=$confidence, r.source_id=$provenance_id",
                    source_id=self.scope(relation.source_entity_id),
                    target_id=self.scope(relation.target_entity_id),
                    fact_id=self.scope(relation.fact_id),
                    provenance_id=self.scope(relation.source_id),
                    tenant=tenant,
                    owner=next(
                        entity.owner_id for entity in batch.entities
                        if entity.id == relation.source_entity_id
                    ),
                    confidence=relation.confidence,
                )

            self._execute_write(write_relationship)

    def search_semantic_memory(self, owner_id: str, query: str, limit: int = 10):
        """Bounded, tenant/owner-scoped confirmed fact retrieval; no caller Cypher."""
        terms = [term for term in query.casefold().split() if term][:10]
        if not terms:
            return []

        def read(tx):
            rows = tx.run(
                "MATCH (:User)-[:HAS_FACT]->(f:SemanticFact)-[:SUPPORTED_BY]->(s:ConversationSource) "
                "WHERE f.tenant_id=$tenant AND f.owner_id=$owner AND f.status='confirmed' "
                "AND any(term IN $terms WHERE toLower(f.predicate) CONTAINS term "
                "OR toLower(f.value) CONTAINS term) "
                "RETURN coalesce(f.original_id,f.id) AS id, f.predicate AS predicate, "
                "f.value AS value, f.confidence AS confidence, s.source_id AS source_id "
                "LIMIT $limit",
                tenant=self.tenant(), owner=owner_id, terms=terms,
                limit=min(max(limit, 1), 20),
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

    def delete_workspace_graph(self, tenant_id: str, batch_size: int = 500) -> dict:
        """Delete only nodes carrying an exact tenant owner marker, in bounded batches."""
        if not tenant_id or self.tenant_provider and tenant_id != self.tenant():
            raise PermissionError("Graph reset tenant mismatch")

        def count(tx):
            return tx.run(
                "MATCH (n) WHERE n.tenant_id=$tenant_id RETURN count(n) AS total",
                tenant_id=tenant_id,
            ).single()["total"]

        total = int(self._execute_read(count) or 0)
        deleted = 0
        while True:
            def remove(tx):
                return tx.run(
                    "MATCH (n) WHERE n.tenant_id=$tenant_id WITH n LIMIT $limit "
                    "DETACH DELETE n RETURN count(*) AS deleted",
                    tenant_id=tenant_id, limit=min(max(batch_size, 1), 1000),
                ).single()["deleted"]

            removed = int(self._execute_write(remove) or 0)
            deleted += removed
            if removed == 0:
                break
        remaining = int(self._execute_read(count) or 0)
        return {"status": "cleared" if remaining == 0 else "incomplete", "deleted": deleted, "before": total, "remaining": remaining}

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

    def link_website_document(self, *args, **kwargs):
        return {"outbound": 0, "inbound": 0, "document_found": False}

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

    def upsert_pages(self, *args, **kwargs):
        pass

    def upsert_reference_objects(self, *args, **kwargs):
        pass

    def link_reference_objects_many(self, *args, **kwargs):
        pass

    def search_relationships(self, entities, limit=12):
        return []

    def find_entity_candidates(self, *args, **kwargs): return []
    def get_entity_neighborhood(self, entity_id, **kwargs): return {"entity_id": entity_id, "relationships": []}
    def find_related_documents(self, *args, **kwargs): return []
    def existing_entity_ids(self, entity_ids): return set()
    def upsert_knowledge_entities(self, entities): return {"upserted": 0}
    def upsert_knowledge_relationships(self, relationships): return {"upserted": 0}
    def link_document_mentions(self, document_id, mentions): return {"linked": 0}
    def update_document_semantics(self, *args, **kwargs): return None

    def upsert_semantic_memory(self, *args, **kwargs):
        return None

    def search_semantic_memory(self, *args, **kwargs):
        return []

    def ping(self):
        return False

    def delete_workspace_graph(self, tenant_id, batch_size=500):
        return {"status": "unavailable", "deleted": 0, "remaining": None}


class InMemoryGraphRepository:
    """Minimal in-memory substitute implementing the GraphRepository interface."""

    def __init__(self) -> None:
        """Initialise empty dictionaries for documents, chunks, and entities."""
        self.documents: MutableMapping[str, Dict[str, Optional[str]]] = {}
        self.chunks: MutableMapping[str, Dict[str, Any]] = {}
        self.entity_links: MutableMapping[str, Dict[str, Any]] = {}
        self.references: MutableMapping[str, Dict[str, Any]] = {}
        self.semantic_facts: MutableMapping[str, Dict[str, Any]] = {}
        self.knowledge_relationships: list[dict] = []
        self.document_mentions: list[dict] = []
        self.websites: MutableMapping[str, set] = {}
        self.document_links: set[tuple[str, str]] = set()
        self.reference_links: set[tuple[str, str, str]] = set()

    def ensure_constraints(self) -> None:  # pragma: no cover - no-op for in-memory variant
        """In-memory store has no constraints to create."""

    def delete_workspace_graph(self, tenant_id, batch_size=500):
        deleted = sum(map(len, (self.documents, self.chunks, self.entity_links, self.references, self.semantic_facts)))
        self.documents.clear(); self.chunks.clear(); self.entity_links.clear(); self.references.clear(); self.semantic_facts.clear()
        self.knowledge_relationships.clear(); self.document_mentions.clear()
        self.websites.clear(); self.document_links.clear(); self.reference_links.clear()
        return {"status": "cleared", "deleted": deleted, "remaining": 0}

    def upsert_document(
        self, doc_id: str, title: Optional[str] = None, source: str = "paste", **metadata
    ) -> None:
        """Store document metadata in-memory."""

        self.documents[doc_id] = {
            "title": title, "source": source,
            **{key: metadata.get(key) for key in ("source_type", "source_url", "canonical_url")},
        }

    def link_website_document(self, doc_id, origin, canonical_url, outlinks):
        if doc_id not in self.documents:
            # Like the Neo4j MATCH: an absent page Document gets no links.
            return {"outbound": 0, "inbound": 0, "document_found": False}
        self.websites.setdefault(origin, set()).add(doc_id)
        self.documents[doc_id]["outlinks"] = list(outlinks or [])
        outbound = {
            (doc_id, other_id) for other_id, other in self.documents.items()
            if other_id != doc_id and other.get("canonical_url") in (outlinks or [])
        }
        inbound = {
            (other_id, doc_id) for other_id, other in self.documents.items()
            if other_id != doc_id and canonical_url in (other.get("outlinks") or [])
        }
        outbound, inbound = outbound - self.document_links, inbound - self.document_links
        self.document_links |= outbound | inbound
        return {"outbound": len(outbound), "inbound": len(inbound), "document_found": True}

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
            "role": item.get("role"),
            "state_flag": item.get("state_flag"),
            "confidence": item.get("confidence"),
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

    def link_reference_objects(self, source_id: str, target_id: str, relationship: str, *labels):
        if source_id not in self.references or target_id not in self.references:
            raise ValueError("Unknown reference object")
        self.reference_links.add((source_id, relationship, target_id))

    def upsert_pages(self, doc_id: str, pages, version: int = 1) -> None:
        """Pages are implicit in the lightweight test repository."""

    def upsert_reference_objects(self, doc_id: str, items) -> None:
        for item in items:
            self.upsert_reference_object(doc_id, item)

    def link_reference_objects_many(self, links) -> None:
        for source_id, target_id, relationship, *_labels in links:
            self.link_reference_objects(source_id, target_id, relationship)

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

    def find_entity_candidates(self, name, entity_type=None, aliases=None, identifiers=None, limit=20):
        terms = {name.casefold(), *(value.casefold() for value in aliases or [])}
        return [{"id": key, "name": value["name"], "kind": value.get("kind") or value.get("type")}
                for key, value in self.entity_links.items()
                if value["name"].casefold() in terms][:limit]

    def get_entity_neighborhood(self, entity_id, max_hops=2, limit=30):
        return {"entity_id": entity_id, "relationships": [row for row in self.knowledge_relationships
                if entity_id in {row.get("source_id"), row.get("target_id")}][:limit]}

    def find_related_documents(self, entity_id, limit=20):
        rows = []
        for mention in self.document_mentions:
            if mention.get("entity_id") == entity_id:
                doc = self.documents.get(mention["document_id"], {})
                rows.append({"document_id": mention["document_id"], "title": doc.get("title"),
                             "summary": doc.get("summary"), "object_ids": mention.get("evidence_object_ids", [])})
        return rows[:limit]

    def existing_entity_ids(self, entity_ids):
        return {value for value in entity_ids if value in self.entity_links}

    def upsert_knowledge_entities(self, entities):
        for item in entities:
            self.entity_links[item["id"]] = {**item, "type": item["kind"], "chunks": set()}
        return {"upserted": len(entities)}

    def upsert_knowledge_relationships(self, relationships):
        self.knowledge_relationships.extend(relationships)
        return {"upserted": len(relationships)}

    def link_document_mentions(self, document_id, mentions):
        self.document_mentions.extend({**item, "document_id": document_id} for item in mentions)
        return {"linked": len(mentions)}

    def update_document_semantics(self, document_id, *, document_type, summary, status):
        self.documents.setdefault(document_id, {}).update(
            document_type=document_type, summary=summary, semantic_graph_status=status
        )

    def ping(self) -> bool:
        """In-memory store is always available."""

        return True

    def upsert_semantic_memory(self, batch) -> None:
        for fact in batch.facts:
            if fact.supersedes_id in self.semantic_facts:
                self.semantic_facts[fact.supersedes_id]["status"] = "superseded"
            self.semantic_facts[fact.id] = fact.model_dump()

    def search_semantic_memory(self, owner_id, query, limit=10):
        terms = query.casefold().split()
        return [
            fact for fact in self.semantic_facts.values()
            if fact["owner_id"] == owner_id and fact["status"] == "confirmed"
            and any(term in (fact["predicate"] + " " + fact["value"]).casefold() for term in terms)
        ][:limit]


def _safe_rel(rel: str) -> str:
    """Return a Neo4j relationship label comprised of safe characters only."""

    candidate = (rel or "ABOUT").upper()
    if not re.match(r"^[A-Z_][A-Z0-9_]*$", candidate):
        return "ABOUT"
    return candidate
