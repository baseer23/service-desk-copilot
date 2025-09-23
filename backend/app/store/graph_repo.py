from __future__ import annotations

import re
from typing import Optional


class GraphRepository:
    def __init__(self, driver) -> None:
        self._driver = driver

    def ensure_constraints(self) -> None:
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
        def _tx(tx):
            tx.run(
                "MERGE (d:Document {id: $doc_id}) "
                "SET d.title = $title, d.source = $source, d.updated_at = timestamp()",
                doc_id=doc_id,
                title=title,
                source=source,
            )

        self._execute_write(_tx)

    def upsert_chunk(self, doc_id: str, chunk_id: str, ord: int, text: str, token_count: int) -> None:
        def _tx(tx):
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
        def _tx(tx):
            tx.run(
                "MATCH (d:Document {id: $doc_id}), (c:Chunk {id: $chunk_id}) "
                "MERGE (d)-[:HAS_CHUNK]->(c)",
                doc_id=doc_id,
                chunk_id=chunk_id,
            )

        self._execute_write(_tx)

    def upsert_entity(self, name: str, type: str = "TERM") -> str:
        def _tx(tx):
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
        rel_type = _safe_rel(rel)

        def _tx(tx):
            tx.run(
                f"MATCH (c:Chunk {{id: $chunk_id}}), (e:Entity {{id: $entity_id}}) "
                f"MERGE (c)-[:{rel_type}]->(e)",
                chunk_id=chunk_id,
                entity_id=entity_id,
            )

        self._execute_write(_tx)

    def get_entity_degrees(self, names):
        if not names:
            return {}

        lower_names = [name.lower() for name in names]

        def _tx(tx):
            result = tx.run(
                "MATCH (e:Entity) WHERE e.id IN $ids "
                "OPTIONAL MATCH (e)--(n) "
                "RETURN e.id AS id, count(n) AS degree",
                ids=lower_names,
            )
            return {record["id"]: record["degree"] for record in result}

        degrees = self._execute_read(_tx)
        # ensure every requested name appears in result
        return {name: degrees.get(name.lower(), 0) for name in names}

    def fetch_chunks_for_entities(self, names, limit):
        if not names:
            return []

        lower_names = [name.lower() for name in names]

        def _tx(tx):
            result = tx.run(
                "MATCH (e:Entity) WHERE e.id IN $ids "
                "MATCH (e)<-[:ABOUT]-(c:Chunk)<-[:HAS_CHUNK]-(d:Document) "
                "RETURN c.id AS chunk_id, c.text AS text, c.ord AS ord, d.id AS doc_id, d.title AS title",
                ids=lower_names,
            )
            records = []
            for record in result:
                records.append(
                    {
                        "id": record["chunk_id"],
                        "text": record["text"],
                        "metadata": {"doc_id": record["doc_id"], "title": record["title"], "ord": record["ord"]},
                        "score": 0.0,
                    }
                )
            return records

        records = self._execute_read(_tx)
        return records[:limit]

    def _execute_write(self, func):
        if hasattr(self._driver, "execute_write"):
            return self._driver.execute_write(func)
        with self._driver.session() as session:  # pragma: no cover - legacy driver path
            return session.execute_write(func)

    def _execute_read(self, func):
        if hasattr(self._driver, "execute_read"):
            return self._driver.execute_read(func)
        with self._driver.session() as session:  # pragma: no cover - legacy driver path
            return session.execute_read(func)

    def ping(self) -> bool:
        def _tx(tx):
            tx.run("RETURN 1 AS ok")
            return True

        try:
            return bool(self._execute_read(_tx))
        except Exception:  # pragma: no cover - depends on runtime connectivity
            return False


class InMemoryGraphRepository:
    def __init__(self) -> None:
        self.documents = {}
        self.chunks = {}
        self.entity_links = {}

    def ensure_constraints(self) -> None:  # pragma: no cover - no-op
        return

    def upsert_document(self, doc_id: str, title: Optional[str] = None, source: str = "paste") -> None:
        self.documents[doc_id] = {"title": title, "source": source}

    def upsert_chunk(self, doc_id: str, chunk_id: str, ord: int, text: str, token_count: int) -> None:
        self.chunks[chunk_id] = {
            "doc_id": doc_id,
            "text": text,
            "ord": ord,
            "tokens": token_count,
        }

    def link_doc_chunk(self, doc_id: str, chunk_id: str) -> None:  # pragma: no cover - implicit in storage
        return

    def upsert_entity(self, name: str, type: str = "TERM") -> str:
        entity_id = name.lower()
        if entity_id not in self.entity_links:
            self.entity_links[entity_id] = {"name": name, "chunks": set()}
        return entity_id

    def link_chunk_entity(self, chunk_id: str, entity_id: str, rel: str = "ABOUT") -> None:
        info = self.entity_links.setdefault(entity_id, {"name": entity_id, "chunks": set()})
        info["chunks"].add(chunk_id)

    def get_entity_degrees(self, names):
        degrees = {}
        for name in names:
            entity_id = name.lower()
            chunks = self.entity_links.get(entity_id, {"chunks": set()})["chunks"]
            degrees[name] = len(chunks)
        return degrees

    def fetch_chunks_for_entities(self, names, limit):
        collected = []
        for name in names:
            entity_id = name.lower()
            for chunk_id in self.entity_links.get(entity_id, {"chunks": set()})["chunks"]:
                chunk = self.chunks.get(chunk_id)
                if not chunk:
                    continue
                metadata = {
                    "doc_id": chunk["doc_id"],
                    "title": self.documents.get(chunk["doc_id"], {}).get("title"),
                    "ord": chunk["ord"],
                }
                collected.append({"id": chunk_id, "text": chunk["text"], "metadata": metadata, "score": 0.0})
        return collected[:limit]

    def ping(self) -> bool:
        return True


def _safe_rel(rel: str) -> str:
    candidate = (rel or "ABOUT").upper()
    if not re.match(r"^[A-Z_][A-Z0-9_]*$", candidate):
        return "ABOUT"
    return candidate
