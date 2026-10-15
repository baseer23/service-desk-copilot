"""Additive migration to local embeddings; source collections/files remain untouched."""

import hashlib
import json
from collections import Counter

from backend.app.adapters.embeddings import get_embedding_provider
from backend.app.core.principal import Principal, principal_scope
from backend.app.services.capabilities import collection_identity, knowledge_stores
from backend.app.store.local_records import LocalRecords
from backend.app.services.knowledge_policy import is_test_knowledge


def collect_source_chunks(client, target_name, records):
    """Return actual stored text with tenant authority and stable original chunk IDs."""
    chunks, counts, quarantine = {}, Counter(), []
    collections = sorted(client.list_collections(), key=lambda c: (not c.name.startswith("chunks_azure_"), c.name))
    for collection in collections:
        if collection.name == target_name:
            continue
        if not collection.name.startswith(("chunks_", "archive_")) and collection.name != "chunks":
            continue
        offset = 0
        while True:
            result = collection.get(limit=200, offset=offset, include=["documents", "metadatas"])
            for index, identifier in enumerate(result["ids"]):
                stored = result["metadatas"][index] or {}
                tenant = stored.get("tenant_id")
                if not tenant:
                    counts["unscoped_legacy_preserved"] += 1
                    continue
                metadata = json.loads(stored["metadata_json"]) if stored.get("metadata_json") else dict(stored)
                if is_test_knowledge(metadata):
                    counts["explicit_fixtures_preserved_but_not_indexed"] += 1
                    quarantine.append({"source_collection": collection.name, "physical_id": identifier, "chunk_id": stored.get("chunk_id", identifier), "doc_id": metadata.get("doc_id")})
                    continue
                chunk_id = stored.get("chunk_id", identifier)
                text = result["documents"][index]
                if not isinstance(text, str) or not text.strip():
                    counts["empty_preserved"] += 1
                    continue
                key = (tenant, chunk_id)
                if key in chunks:
                    if chunks[key]["text"] != text:
                        raise ValueError("Conflicting historical chunk text requires review before indexing")
                    counts["duplicate_source_rows"] += 1
                    continue
                chunks[key] = {"id": chunk_id, "text": text, "metadata": metadata, "source_collection": collection.name}
                counts["source_rows"] += 1
            if len(result["ids"]) < 200:
                break
            offset += 200
    with records.connection() as db:
        rows = db.execute("SELECT tenant,body FROM local_records WHERE kind='knowledge_pending'").fetchall()
    for tenant, body in rows:
        row = json.loads(body)
        chunk = row["payload"]
        if is_test_knowledge(chunk.get("metadata", {})):
            counts["explicit_fixtures_preserved_but_not_indexed"] += 1
            continue
        key = (tenant, chunk["id"])
        if key in chunks and chunks[key]["text"] != chunk["text"]:
            raise ValueError("Pending knowledge conflicts with preserved source text")
        chunks.setdefault(key, {**chunk, "source_collection": "transactional_staging"})
    return chunks, counts, quarantine


def migrate_local_embeddings(settings, *, apply=False, provider=None, enrich_graph=False):
    """Dry-run by default; never contact Azure, erase vectors or overwrite originals."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    if settings.embed_provider != "sentence_transformers" or not settings.local_persistence_enabled:
        raise ValueError("Select local SentenceTransformers with local persistence before migration")
    if not (settings.chroma_dir / "chroma.sqlite3").is_file():
        raise ValueError("An existing persistent Chroma source is required")
    client = chromadb.PersistentClient(str(settings.chroma_dir), settings=ChromaSettings(anonymized_telemetry=False))
    name, model, _ = collection_identity(settings)
    records = LocalRecords(settings)
    chunks, counts, quarantine = collect_source_chunks(client, name, records)
    sources = {c.name: c.count() for c in client.list_collections() if c.name != name}
    fingerprints = [hashlib.sha256((tenant + "\0" + key + "\0" + chunk["text"]).encode()).hexdigest() for (tenant, key), chunk in chunks.items()]
    report = {
        "dry_run": not apply, "source_collections": sources,
        "source_chunks": len(chunks), "tenant_count": len({key[0] for key in chunks}),
        "source_digest": hashlib.sha256("".join(sorted(fingerprints)).encode()).hexdigest(),
        "target_collection": name, "provider": "sentence_transformers", "model": model,
        "dimensions": 384, "originals_modified": 0, "source_collections_deleted": 0,
        "audit_counts": dict(counts), "quarantined_chunks": quarantine,
        "indexed": 0, "graph_available": False,
    }
    if not apply:
        return report
    provider = provider or get_embedding_provider(settings)
    vector, graph, driver = knowledge_stores(settings, connect_graph=enrich_graph, records=records)
    report["graph_available"] = bool(driver and graph.ping())
    try:
        # Explicitly labeled fixtures are copied to excluded archival collections,
        # without removing a single source row or changing any original file.
        for item in quarantine:
            source = client.get_collection(item["source_collection"], embedding_function=None)
            source_row = source.get(ids=[item["physical_id"]], include=["documents", "metadatas", "embeddings"])
            archive_name = "quarantine_" + hashlib.sha256(item["source_collection"].encode()).hexdigest()[:16]
            archive = client.get_or_create_collection(archive_name, embedding_function=None,
                metadata={"purpose": "excluded_explicit_test_material", "source_collection": item["source_collection"]})
            archive.upsert(ids=source_row["ids"], documents=source_row["documents"],
                metadatas=source_row["metadatas"], embeddings=source_row["embeddings"])
            copied = archive.get(ids=source_row["ids"], include=["documents", "metadatas"])
            if copied["documents"] != source_row["documents"] or copied["metadatas"] != source_row["metadatas"]:
                raise ValueError("Quarantine archive verification failed")
        keys = sorted(chunks)
        for start in range(0, len(keys), 32):
            batch_keys = keys[start:start + 32]
            batch = [chunks[key] for key in batch_keys]
            embeddings = provider.embed_texts([chunk["text"] for chunk in batch])
            if len(embeddings) != len(batch):
                raise ValueError("Local embedding migration returned an incomplete batch")
            for (tenant, _), chunk, embedding in zip(batch_keys, batch, embeddings, strict=True):
                previous = chunk["metadata"]
                metadata = {
                    **previous,
                    "previous_embedding_provider": previous.get("embedding_provider", "unknown"),
                    "previous_embedding_model": previous.get("embedding_model", "unknown"),
                    "embedding_provider": "sentence_transformers", "embedding_model": model,
                    "embedding_migration_source": chunk["source_collection"],
                }
                with principal_scope(Principal(tenant, "local-embedding-migration")):
                    vector.upsert([{**chunk, "metadata": metadata, "embedding": embedding}])
                    if report["graph_available"]:
                        from backend.app.services.entities import extract_entities
                        doc_id = metadata.get("doc_id", chunk["id"])
                        graph.upsert_document(doc_id, metadata.get("title"), metadata.get("source", "local_document"))
                        graph.upsert_chunk(doc_id, chunk["id"], int(metadata.get("ord", 0)), chunk["text"], max(1, len(chunk["text"]) // 4))
                        graph.link_doc_chunk(doc_id, chunk["id"])
                        for entity in extract_entities([chunk]):
                            graph.link_chunk_entity(chunk["id"], graph.upsert_entity(entity))
                report["indexed"] += 1
        for collection in client.list_collections():
            if collection.name in sources and collection.count() != sources[collection.name]:
                raise ValueError("A preserved source collection changed during migration")
        # Verify every target text, metadata provenance and vector dimensionality.
        for (tenant, identifier), chunk in chunks.items():
            with principal_scope(Principal(tenant, "local-embedding-migration")):
                found = vector.vector._collection.get(ids=[vector.physical_id(identifier)], include=["documents", "metadatas", "embeddings"])
                if found["documents"] != [chunk["text"]] or len(found["embeddings"][0]) != 384:
                    raise ValueError("Local embedding migration verification failed")
                metadata = json.loads(found["metadatas"][0]["metadata_json"])
                if metadata.get("embedding_provider") != "sentence_transformers" or metadata.get("embedding_model") != model:
                    raise ValueError("Local embedding provenance verification failed")
        report["verified"] = True
        report["graph_available"] = bool(driver and graph.ping())
        return report
    finally:
        if driver:
            driver.close()
