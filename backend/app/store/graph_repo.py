from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, MutableMapping, Optional, Sequence, Set, Tuple, TypeVar
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

    def __init__(self, driver: Driver) -> None:
        """Store the Neo4j driver for subsequent transactional work."""
        self._driver: Driver = driver

    def ensure_constraints(self) -> None:
        """Create Neo4j constraints and indexes required for ingest operations."""

        statements = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
            "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.name)",
        ]
        with self._driver.session() as session:
            for statement in statements:
                session.run(statement)

    def upsert_document(self, doc_id: str, title: Optional[str] = None, source: str = "paste") -> None:
        """Insert or update a document node and basic metadata."""

        def _tx(tx: Transaction) -> None:
            tx.run(
                "MERGE (d:Document {id: $doc_id}) "
                "SET d.title = $title, d.source = $source, d.updated_at = timestamp()",
                doc_id=doc_id,
                title=title,
                source=source,
            )

        self._execute_write(_tx)

    def upsert_chunk(self, doc_id: str, chunk_id: str, ord: int, text: str, token_count: int) -> None:
        """Insert or update a chunk node with ordering and token metadata."""

        def _tx(tx: Transaction) -> None:
            tx.run(
                "MERGE (c:Chunk {id: $chunk_id}) "
                "SET c.ord = $ord, c.text = $text, c.tokens = $token_count, c.updated_at = timestamp()",
                chunk_id=chunk_id,
                ord=ord,
                text=text,
                token_count=token_count,
            )

        self._execute_write(_tx)

    def link_doc_chunk(self, doc_id: str, chunk_id: str) -> None:
        """Ensure the relationship between a document and one of its chunks exists."""

        def _tx(tx: Transaction) -> None:
            tx.run(
                "MATCH (d:Document {id: $doc_id}), (c:Chunk {id: $chunk_id}) "
                "MERGE (d)-[:HAS_CHUNK]->(c)",
                doc_id=doc_id,
                chunk_id=chunk_id,
            )

        self._execute_write(_tx)

    def upsert_entity(self, name: str, type: str = "TERM") -> str:
        """Insert or update an entity node, returning its canonical identifier."""

        def _tx(tx: Transaction) -> str:
            result = tx.run(
                "MERGE (e:Entity {id: toLower($name)}) "
                "SET e.name = $name, e.type = $type, e.updated_at = timestamp() "
                "RETURN e.id AS id",
                name=name,
                type=type,
            )
            record = result.single()
            return record["id"] if record else name.lower()

        result = self._execute_write(_tx)
        if isinstance(result, str):
            return result
        return str(result)

    def link_chunk_entity(self, chunk_id: str, entity_id: str, rel: str = "ABOUT") -> None:
        """Create a relationship between a chunk and an entity."""

        rel_type = _safe_rel(rel)

        def _tx(tx: Transaction) -> None:
            tx.run(
                f"MATCH (c:Chunk {{id: $chunk_id}}), (e:Entity {{id: $entity_id}}) "
                f"MERGE (c)-[:{rel_type}]->(e)",
                chunk_id=chunk_id,
                entity_id=entity_id,
            )

        self._execute_write(_tx)

    def get_entity_degrees(self, names: Iterable[str]) -> Dict[str, int]:
        """Return a mapping of entity id to degree counts for the requested names."""

        name_list = list(names)
        if not name_list:
            return {}

        lower_names = [name.lower() for name in name_list]

        def _tx(tx: Transaction) -> Dict[str, int]:
            result = tx.run(
                "MATCH (e:Entity) WHERE e.id IN $ids "
                "OPTIONAL MATCH (e)--(n) "
                "RETURN e.id AS id, count(n) AS degree",
                ids=lower_names,
            )
            return {record["id"]: record["degree"] for record in result}

        degrees = self._execute_read(_tx)
        return {name: degrees.get(name.lower(), 0) for name in name_list}

    def fetch_chunks_for_entities(self, names: Sequence[str], limit: int) -> List[ChunkRecord]:
        """Fetch chunks connected to any of the provided entity names."""

        if not names:
            return []

        lower_names = [name.lower() for name in names]

        def _tx(tx: Transaction) -> List[ChunkRecord]:
            result = tx.run(
                "MATCH (e:Entity) WHERE e.id IN $ids "
                "MATCH (e)<-[:ABOUT]-(c:Chunk)<-[:HAS_CHUNK]-(d:Document) "
                "RETURN c.id AS chunk_id, c.text AS text, c.ord AS ord, d.id AS doc_id, d.title AS title",
                ids=lower_names,
            )
            records: List[ChunkRecord] = []
            for record in result:
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
        return records[:limit]

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
        """Execute a write transaction using the best available driver API."""

        if hasattr(self._driver, "execute_write"):
            return self._driver.execute_write(func)
        with self._driver.session() as session:  # pragma: no cover - legacy driver path
            return session.execute_write(func)  # type: ignore[call-arg]

    def _execute_read(self, func: Callable[[Transaction], T]) -> T:
        """Execute a read transaction using the best available driver API."""

        if hasattr(self._driver, "execute_read"):
            return self._driver.execute_read(func)
        with self._driver.session() as session:  # pragma: no cover - legacy driver path
            return session.execute_read(func)  # type: ignore[call-arg]


class InMemoryGraphRepository:
    """Minimal in-memory substitute implementing the GraphRepository interface."""

    def __init__(self) -> None:
        """Initialise empty dictionaries for documents, chunks, and entities."""
        self.documents: MutableMapping[str, Dict[str, Optional[str]]] = {}
        self.chunks: MutableMapping[str, Dict[str, Any]] = {}
        self.entity_links: MutableMapping[str, Dict[str, Any]] = {}

    def ensure_constraints(self) -> None:  # pragma: no cover - no-op for in-memory variant
        """In-memory store has no constraints to create."""

    def upsert_document(self, doc_id: str, title: Optional[str] = None, source: str = "paste") -> None:
        """Store document metadata in-memory."""

        self.documents[doc_id] = {"title": title, "source": source}

    def upsert_chunk(self, doc_id: str, chunk_id: str, ord: int, text: str, token_count: int) -> None:
        """Persist chunk metadata in-memory for testing."""

        self.chunks[chunk_id] = {
            "doc_id": doc_id,
            "text": text,
            "ord": ord,
            "tokens": token_count,
        }

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

        info = self.entity_links.setdefault(entity_id, {"name": entity_id, "type": "TERM", "chunks": set()})
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
            for chunk_id in self.entity_links.get(entity_id, {"chunks": set()}).get("chunks", set()):
                chunk = self.chunks.get(chunk_id)
                if not chunk:
                    continue
                metadata = {
                    "doc_id": chunk["doc_id"],
                    "title": self.documents.get(chunk["doc_id"], {}).get("title"),
                    "ord": chunk.get("ord"),
                }
                collected.append({"id": chunk_id, "text": chunk["text"], "metadata": metadata, "score": 0.0})
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
