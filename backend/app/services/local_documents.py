"""Local originals, text extraction, Chroma retrieval and transactional jobs."""

import hashlib

from backend.app.services.document_repository import DocumentRepository
from backend.app.services.capabilities import knowledge_stores
from backend.app.store.local_knowledge import LocalDocuments
from backend.app.store.local_records import LocalRecords


def graph_writer_readiness(settings):
    """Verify the separately authorized ingestion writer and required schema."""
    configured = (
        settings.neo4j_ingestion_enabled
        and settings.neo4j_uri
        and settings.neo4j_ingestion_user
        and settings.neo4j_ingestion_password.get_secret_value()
    )
    if not configured:
        return {
            "available": False,
            "blocker": "Configure an authorized Neo4j ingestion connection to enable linking.",
        }
    from neo4j import GraphDatabase
    from backend.app.store.graph_repo import GraphRepository

    driver = None
    try:
        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(
                settings.neo4j_ingestion_user,
                settings.neo4j_ingestion_password.get_secret_value(),
            ),
            connection_timeout=5,
            max_transaction_retry_time=0,
            connection_acquisition_timeout=5,
        )
        driver.verify_connectivity()
        graph = GraphRepository(driver, optional=False, database=settings.neo4j_database)
        graph.ensure_constraints()
        with driver.session(database=settings.neo4j_database) as session:
            transaction = session.begin_transaction()
            try:
                transaction.run(
                    "MERGE (n:DeskMateWriterProbe {id:$id}) SET n.checked_at=timestamp()",
                    id="permission-check",
                ).consume()
            finally:
                transaction.rollback()
        return {"available": True, "blocker": None}
    except Exception:
        return {
            "available": False,
            "blocker": "Neo4j ingestion credentials could not create reference metadata.",
        }
    finally:
        if driver:
            driver.close()


class LocalDocumentExtractor:
    def __init__(self, settings, records):
        self.settings, self.records = settings, records

    def extract(self, data, media_type, enhanced=False, force_azure=False):
        # Native extraction avoids uploading ordinary PDFs to any cloud service.
        if media_type == "application/pdf" and not force_azure:
            from backend.app.services.local_pdf import extract_pdf_pages

            pages = extract_pdf_pages(data)
            if pages and all(page["text"].strip() for page in pages):
                return {
                    "pages": [{**page, "markdown": page["text"], "spans": []} for page in pages],
                    "paragraphs": [],
                    "sections": [],
                    "tables": [],
                    "figures": [],
                    "method": "pdf_text",
                    "metadata": {
                        "structured": enhanced,
                        "image_understanding": "unsupported_local_extraction",
                    },
                }
        if not self.settings.azure_document_endpoint:
            raise ValueError("Document processing is not configured for scanned or image documents")
        from backend.app.services.document_processing import AzureDocumentExtractor

        extractor = AzureDocumentExtractor(self.settings, self.records)
        try:
            return extractor.extract(data, media_type, enhanced=enhanced)
        finally:
            extractor.client.close()
            if extractor.credential:
                extractor.credential.close()

    def extract_path(self, path, media_type, enhanced=False, force_azure=False, **callbacks):
        if media_type == "application/pdf" and not force_azure:
            from backend.app.services.local_pdf import extract_pdf_pages_path

            pages = extract_pdf_pages_path(path)
            if pages and all(page["text"].strip() for page in pages):
                return {
                    "pages": [{**page, "markdown": page["text"], "spans": []} for page in pages],
                    "paragraphs": [], "sections": [], "tables": [], "figures": [],
                    "method": "pdf_text",
                    "metadata": {"structured": enhanced, "image_understanding": "unsupported_local_extraction"},
                }
        if not self.settings.azure_document_endpoint:
            raise ValueError("Document processing is not configured for scanned or image documents")
        from backend.app.services.document_processing import AzureDocumentExtractor

        extractor = AzureDocumentExtractor(self.settings, self.records)
        try:
            return extractor.extract_path(path, media_type, enhanced=enhanced, **callbacks)
        finally:
            extractor.client.close()
            if extractor.credential:
                extractor.credential.close()


class LocalDocumentRepository(DocumentRepository):
    def __init__(self, settings, records=None, *, extractor=None, embeddings=None):
        records = records or LocalRecords(settings)
        vector, _, _ = knowledge_stores(settings, connect_graph=False, records=records)
        super().__init__(
            settings,
            records=records,
            blobs=LocalDocuments(settings, records),
            search=vector,
            extractor=extractor or LocalDocumentExtractor(settings, records),
            embeddings=embeddings,
        )

    def process(self, identifier):
        existing = self.get(identifier)
        if (
            existing.get("ingestion_mode") == "structured_graph"
            and existing.get("phase") == "link"
            and existing.get("status") in {"linking", "graph_pending"}
        ):
            return self._link_structured(identifier, existing)
        state = super().process(identifier)
        if state.get("ingestion_mode") == "structured_graph":
            return self._link_structured(identifier, state)
        # Standard ingestion is local-only. Aura writes require the explicit
        # structured_graph mode and its separately authorized writer.
        return state

    def enrich_graph(self, identifier):
        """Index saved extraction as structured objects, then idempotently link it."""
        state = self.get(identifier)
        if state.get("ingestion_mode") == "structured_graph" and state.get("phase") == "link":
            return self._link_structured(identifier, state)
        self.prepare_graph_enrichment(identifier)
        return self.process(identifier)

    def _link_structured(self, identifier, state):
        """Publish reference-only structure with explicitly separate writer credentials."""
        import json
        from backend.app.services.chunking import split_structured

        configured = (
            self.settings.neo4j_ingestion_enabled
            and self.settings.neo4j_uri
            and self.settings.neo4j_ingestion_user
            and self.settings.neo4j_ingestion_password.get_secret_value()
        )
        if not configured and not self.settings.test_mode:
            return self._set_graph_status(identifier, state, "graph_pending")
        driver = None
        try:
            if self.settings.test_mode:
                from backend.app.store.graph_repo import InMemoryGraphRepository

                graph = InMemoryGraphRepository()
            else:
                from neo4j import GraphDatabase
                from backend.app.core.principal import current_principal
                from backend.app.store.graph_repo import GraphRepository

                driver = GraphDatabase.driver(
                    self.settings.neo4j_uri,
                    auth=(
                        self.settings.neo4j_ingestion_user,
                        self.settings.neo4j_ingestion_password.get_secret_value(),
                    ),
                    connection_timeout=5,
                    max_transaction_retry_time=0,
                    connection_acquisition_timeout=5,
                )
                driver.verify_connectivity()
                graph = GraphRepository(
                    driver,
                    tenant_provider=lambda: current_principal(self.settings).tenant_id,
                    optional=False,
                    allow_fixtures=self.settings.test_mode,
                    database=self.settings.neo4j_database,
                )
                graph.ensure_constraints()
            extraction = json.loads(
                self.blobs.get(identifier, state.get("extraction_artifact", "extraction.json"))
            )
            graph.upsert_document(
                identifier, str(state.get("title") or "Untitled")[:160],
                "structured_local_document",
                version=state.get("version", 1),
                content_hash=state.get("content_hash", identifier),
            )
            objects = split_structured(identifier, extraction, self.settings.chunk_tokens)
            pages = sorted({item.get("page") for item in objects if item.get("page") is not None})
            for page in pages:
                graph.upsert_page(identifier, page, state.get("version", 1))
            for item in objects:
                description = (
                    f"{item['object_type']} reference for "
                    f"{str(state.get('title') or 'Untitled')[:100]}"
                    f" on page {item.get('page')}"
                )
                graph.upsert_reference_object(
                    identifier,
                    {
                        **item,
                        # Never send private source excerpts or table values to Aura.
                        "description": description,
                        "version": state.get("version", 1),
                        "content_hash": hashlib.sha256(item["text"].encode()).hexdigest(),
                        "source_provenance": "azure_document_intelligence"
                        if state.get("document_processing") == "azure_di"
                        else "saved_local_extraction",
                        "link_status": "linked",
                    },
                )
            sections = {
                item.get("source_id"): item["object_id"]
                for item in objects
                if item["object_type"] == "Section"
            }
            chunks = [item for item in objects if item["object_type"] == "Chunk"]
            for chunk in chunks:
                if chunk.get("section_id") in sections:
                    graph.link_reference_objects(
                        sections[chunk["section_id"]], chunk["object_id"], "HAS_CHUNK"
                    )
            return self._set_graph_status(identifier, state, "ready")
        except Exception:
            return self._set_graph_status(identifier, state, "graph_pending")
        finally:
            if driver:
                driver.close()

    def _set_graph_status(self, identifier, state, status):
        row = self.records.read("document", identifier)
        updated = {
            **state,
            "status": status,
            "phase": "complete" if status == "ready" else "link",
            "local_index_ready": bool(state.get("chunks")),
            "graph_link_status": "linked" if status == "ready" else "pending",
        }
        self.records.write("document", identifier, updated, previous=row, fields={"status": status})
        return updated
