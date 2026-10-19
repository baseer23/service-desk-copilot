"""Local originals, text extraction, Chroma retrieval and transactional jobs."""

from backend.app.services.document_repository import DocumentRepository
from backend.app.services.capabilities import knowledge_stores
from backend.app.store.local_knowledge import LocalDocuments
from backend.app.store.local_records import LocalRecords


class LocalDocumentExtractor:
    def __init__(self, settings, records):
        self.settings, self.records = settings, records

    def extract(self, data, media_type, enhanced=False):
        # Native extraction avoids uploading ordinary PDFs to any cloud service.
        if media_type == "application/pdf":
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
        state = super().process(identifier)
        if state.get("ingestion_mode") == "structured_graph":
            return self._link_structured(identifier, state)
        # Standard ingestion is local-only. Aura writes require the explicit
        # structured_graph mode and its separately authorized writer.
        return state

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
                )
                graph.ensure_constraints()
            extraction = json.loads(self.blobs.get(identifier, "extraction.json"))
            graph.upsert_document(
                identifier, str(state.get("title") or "Untitled")[:160],
                "structured_local_document",
            )
            objects = split_structured(identifier, extraction, self.settings.chunk_tokens)
            for item in objects:
                graph.upsert_reference_object(
                    identifier,
                    {
                        **item,
                        "description": item["text"][:160],
                    },
                )
            sections = {
                item.get("source_id"): item["object_id"]
                for item in objects
                if item["object_type"] == "Section"
            }
            chunks = [item for item in objects if item["object_type"] == "Chunk"]
            tables = [item for item in objects if item["object_type"] == "Table"]
            figures = [item for item in objects if item["object_type"] == "Figure"]
            for chunk in chunks:
                if chunk.get("section_id") in sections:
                    graph.link_reference_objects(
                        sections[chunk["section_id"]], chunk["object_id"], "HAS_CHUNK"
                    )
                for table in tables:
                    if table.get("page") == chunk.get("page"):
                        graph.link_reference_objects(
                            chunk["object_id"], table["object_id"], "REFERENCES_TABLE"
                        )
                for figure in figures:
                    if figure.get("page") == chunk.get("page"):
                        graph.link_reference_objects(
                            chunk["object_id"], figure["object_id"], "REFERENCES_FIGURE"
                        )
            return self._set_graph_status(identifier, state, "ready")
        except Exception:
            return self._set_graph_status(identifier, state, "graph_pending")
        finally:
            if driver:
                driver.close()

    def _set_graph_status(self, identifier, state, status):
        row = self.records.read("document", identifier)
        updated = {**state, "status": status, "phase": "complete" if status == "ready" else "link"}
        self.records.write("document", identifier, updated, previous=row, fields={"status": status})
        return updated
