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

    def preview_graph(self, source, title, media_type):
        """Analyze an unsaved upload and persist reference-only graph metadata.

        No original, extraction JSON, vectors or document record are retained.
        The graph metadata persists intentionally, even after the staged file
        is deleted. A later saved upload of identical bytes reuses its doc ID.
        """
        import hashlib

        if not (
            self.settings.auto_document_graph_ingestion
            and self.settings.azure_document_endpoint
            and self.settings.neo4j_ingestion_enabled
            and self.settings.neo4j_ingestion_user
            and self.settings.neo4j_ingestion_password.get_secret_value()
        ):
            raise ValueError("Azure DI and the Neo4j ingestion writer are required")
        digest = hashlib.sha256()
        with open(source, "rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        identifier = digest.hexdigest()
        extracted = self.extractor.extract_path(
            source, media_type, enhanced=True, force_azure=True
        )
        from backend.app.store.graph_repo import GraphRepository
        from neo4j import GraphDatabase
        from backend.app.core.principal import current_principal

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
        try:
            driver.verify_connectivity()
            graph = GraphRepository(
                driver,
                tenant_provider=lambda: current_principal(self.settings).tenant_id,
                optional=False, allow_fixtures=False,
                database=self.settings.neo4j_database,
            )
            graph.ensure_constraints()
            objects = self._publish_reference_objects(
                graph, identifier, title, extracted, temporary=True
            )
        finally:
            driver.close()
        # Excerpts are returned to the caller only; no local or Aura text copy.
        preview = "\n\n".join(str(page.get("text") or "") for page in extracted["pages"])
        return {
            "id": identifier,
            "text": preview[:40000],
            "pages": len(extracted["pages"]),
            "chunks": 0,
            "graph_objects": objects,
            "graph_link_status": "linked",
            "method": "document_intelligence",
            "saved": False,
            "source_persisted": False,
        }

    def _publish_reference_objects(self, graph, identifier, title, extracted, *, temporary=False, version=1):
        """Project Azure layout-derived, source-grounded structure into Aura.

        All text/values remain outside Neo4j. Links represent actual hierarchy,
        page membership or table-cell containment, not invented semantic facts.
        """
        import hashlib
        from backend.app.services.chunking import split_structured

        objects = split_structured(identifier, extracted, self.settings.chunk_tokens)
        paragraphs = extracted.get("paragraphs", [])
        cells = [
            (table, cell)
            for table in extracted.get("tables", [])
            for cell in table.get("cells", [])
        ]
        if len(objects) + len(paragraphs) + len(cells) > 5000:
            raise ValueError("Document contains too many graph reference objects")
        graph.upsert_document(
            identifier, str(title or "Untitled")[:160],
            "unsaved_azure_layout_references" if temporary else "structured_local_document",
            version=version, content_hash=identifier,
        )
        pages = sorted({
            int(page)
            for page in [
                *[obj.get("page") for obj in objects],
                *[item.get("page") for item in paragraphs],
                *[table.get("page") for table in extracted.get("tables", [])],
            ]
            if page is not None
        })
        for page in pages:
            graph.upsert_page(identifier, page, version)
        for item in objects:
            graph.upsert_reference_object(
                identifier,
                {
                    **item,
                    "description": (
                        f"{item['object_type']} reference on page {item.get('page')}"
                    ),
                    "version": version,
                    "content_hash": hashlib.sha256(item["text"].encode()).hexdigest(),
                    "source_provenance": "azure_document_intelligence",
                    "link_status": "reference_only_unsaved" if temporary else "linked",
                },
            )
        section_map = {
            obj.get("source_id"): obj["object_id"]
            for obj in objects if obj["object_type"] == "Section"
        }
        for obj in objects:
            if obj["object_type"] == "Chunk" and obj.get("section_id") in section_map:
                graph.link_reference_objects(
                    section_map[obj["section_id"]], obj["object_id"], "HAS_CHUNK"
                )
        for section in extracted.get("sections", []):
            parent, child = section_map.get(section.get("parent_id")), section_map.get(section.get("id"))
            if parent and child and parent != child:
                graph.link_reference_objects(parent, child, "HAS_SECTION")
        for paragraph in paragraphs:
            source_id = str(paragraph.get("id") or "")
            paragraph_id = "PAR" + hashlib.sha256(
                f"{identifier}:{source_id}:{paragraph.get('page')}:{paragraph.get('position')}".encode()
            ).hexdigest()[:24]
            graph.upsert_reference_object(
                identifier,
                {
                    "object_id": paragraph_id, "object_type": "Paragraph",
                    "page": paragraph.get("page"), "position": paragraph.get("position"),
                    "description": f"Paragraph role {paragraph.get('role') or 'body'} on page {paragraph.get('page')}",
                    "version": version, "content_hash": hashlib.sha256(
                        str(paragraph.get("text") or "").encode()
                    ).hexdigest(),
                    "source_provenance": "azure_document_intelligence",
                    "link_status": "reference_only_unsaved" if temporary else "linked",
                },
            )
        table_objects = {}
        for obj in objects:
            if obj["object_type"] == "Table":
                table_objects.setdefault(obj.get("table_id"), []).append(obj["object_id"])
        for table, cell in cells:
            cell_id = "CELL" + hashlib.sha256(
                f"{identifier}:{table.get('id')}:{cell.get('row')}:{cell.get('column')}".encode()
            ).hexdigest()[:24]
            graph.upsert_reference_object(
                identifier,
                {
                    "object_id": cell_id, "object_type": "TableCell",
                    "page": table.get("page"),
                    "position": cell.get("row", 0) * 100000 + cell.get("column", 0),
                    "description": f"Cell row {cell.get('row')} column {cell.get('column')} on page {table.get('page')}",
                    "version": version,
                    "content_hash": hashlib.sha256(str(cell.get("text") or "").encode()).hexdigest(),
                    "source_provenance": "azure_document_intelligence",
                    "link_status": "reference_only_unsaved" if temporary else "linked",
                },
            )
            for table_id in table_objects.get(table.get("id"), []):
                graph.link_reference_objects(table_id, cell_id, "HAS_CELL")
        return len(objects) + len(paragraphs) + len(cells)

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
            self._publish_reference_objects(
                graph, identifier, state.get("title"), extraction,
                temporary=False, version=state.get("version", 1),
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
