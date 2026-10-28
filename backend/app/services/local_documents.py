"""Local originals, text extraction, Chroma retrieval and transactional jobs."""

import hashlib
import logging
import traceback
from pathlib import Path

from backend.app.services.document_repository import DocumentRepository
from backend.app.services.capabilities import knowledge_stores
from backend.app.store.local_knowledge import LocalDocuments
from backend.app.store.local_records import LocalRecords

logger = logging.getLogger(__name__)


def graph_writer_readiness(settings):
    """Verify the separately authorized ingestion writer and required schema."""
    missing = []
    if not settings.neo4j_ingestion_enabled:
        missing.append("NEO4J_INGESTION_ENABLED=true")
    if not settings.neo4j_uri:
        missing.append("NEO4J_URI")
    if not settings.neo4j_database:
        missing.append("NEO4J_DATABASE")
    if not settings.neo4j_ingestion_user:
        missing.append("NEO4J_INGESTION_USER")
    if not settings.neo4j_ingestion_password.get_secret_value():
        missing.append("NEO4J_INGESTION_PASSWORD")
    if missing:
        return {
            "available": False,
            "code": "NEO4J_WRITER_CONFIGURATION_MISSING",
            "blocker": "Required Neo4j writer setting(s): " + ", ".join(missing),
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
        return {"available": True, "code": None, "blocker": None}
    except Exception as exc:
        kind = type(exc).__name__
        code = (
            "NEO4J_WRITER_AUTHORIZATION_FAILED"
            if kind in {"AuthError", "ClientError"}
            else "NEO4J_WRITER_UNREACHABLE"
            if kind in {"ServiceUnavailable", "SessionExpired"}
            else "NEO4J_WRITER_VALIDATION_FAILED"
        )
        safe_trace = " <- ".join(
            f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__)[-8:]
        )
        logger.warning(
            "Neo4j writer readiness failed database=%s type=%s code=%s traceback=%s",
            settings.neo4j_database, kind, code, safe_trace,
        )
        return {
            "available": False,
            "code": code,
            "blocker": (
                "Neo4j writer authentication or permissions failed."
                if code == "NEO4J_WRITER_AUTHORIZATION_FAILED"
                else "Neo4j writer connectivity check failed."
                if code == "NEO4J_WRITER_UNREACHABLE"
                else "Neo4j writer schema or transaction validation failed."
            ),
        }
    finally:
        if driver:
            driver.close()


class LocalDocumentExtractor:
    def __init__(self, settings, records):
        self.settings, self.records = settings, records

    def extract(self, data, media_type, enhanced=False, force_azure=False):
        if media_type == "text/html":
            # HTML is structured locally; it must never fall through to Azure.
            raise ValueError("Saved web pages are structured during local indexing")
        if media_type in {"text/plain", "text/markdown"}:
            text = data.decode("utf-8-sig")
            if not text.strip():
                raise ValueError("The document contains no readable text")
            return {
                "pages": [{"page": 1, "text": text, "markdown": text, "spans": []}],
                "paragraphs": [], "sections": [], "tables": [], "figures": [],
                "method": "native_text",
                "metadata": {
                    "format": media_type, "page_count": 1, "table_count": 0,
                    "classification": "text_document",
                    "classification_method": "local_text_fallback",
                    "structured": enhanced,
                    "image_understanding": "not_applicable",
                },
            }
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
        if media_type == "text/html":
            raise ValueError("Saved web pages are structured during local indexing")
        if media_type in {"text/plain", "text/markdown"}:
            return self.extract(path.read_bytes(), media_type, enhanced, force_azure=False)
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
        local_text = media_type in {"text/plain", "text/markdown"}
        if not (
            self.settings.auto_document_graph_ingestion
            and (local_text or self.settings.azure_document_endpoint)
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
            source, media_type, enhanced=True, force_azure=not local_text
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
            graph_counts = self._publish_reference_objects(
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
            "graph_nodes": graph_counts["nodes"],
            "graph_relationships": graph_counts["relationships"],
            "graph_link_status": "linked",
            "method": extracted.get("method", "unknown"),
            "saved": False,
            "source_persisted": False,
        }

    def _publish_reference_objects(self, graph, identifier, title, extracted, *, temporary=False, version=1):
        """Project Azure layout-derived, source-grounded structure into Aura.

        All text/values remain outside Neo4j. Links represent actual hierarchy,
        page membership or table-cell containment, not invented semantic facts.
        """
        from backend.app.services.chunking import split_structured

        objects = split_structured(
            identifier, extracted, self.settings.chunk_tokens, version
        )
        paragraphs = extracted.get("paragraphs", [])
        cells = [
            (table, cell)
            for table in extracted.get("tables", [])
            for cell in table.get("cells", [])
        ]
        if len(objects) + len(paragraphs) + len(cells) > 5000:
            raise ValueError("Document contains too many graph reference objects")
        node_count, relationship_count = 1, 0
        graph.upsert_document(
            identifier, str(title or "Untitled")[:160],
            "unsaved_azure_layout_references" if temporary else "structured_local_document",
            version=version, content_hash=identifier,
        )
        pages = sorted({
            int(page)
            for page in [
                *[page.get("page") for page in extracted.get("pages", [])],
                *[obj.get("page") for obj in objects],
                *[item.get("page") for item in paragraphs],
                *[table.get("page") for table in extracted.get("tables", [])],
            ]
            if page is not None
        })
        section_titles = {
            section.get("id"): str(section.get("title") or "").strip()[:120]
            for section in extracted.get("sections", [])
            if section.get("id") and str(section.get("title") or "").strip()
        }
        document_label = str(title or "Untitled").strip()[:120]

        def reference_description(item):
            kind, page = item["object_type"], item.get("page")
            if kind == "Section":
                return f"{document_label} — section: {str(item.get('text') or '')[:120]}"
            if kind == "Chunk" and section_titles.get(item.get("section_id")):
                return (
                    f"{document_label} — {section_titles[item['section_id']]} — "
                    f"chunk reference on page {page}"
                )
            if kind == "Table":
                return f"{document_label} — table reference on page {page}"
            if kind == "Figure" and str(item.get("text") or "").strip():
                return f"{document_label} — figure: {str(item['text']).strip()[:100]}"
            return f"{document_label} — {kind.lower()} reference on page {page}"

        for page in pages:
            graph.upsert_page(identifier, page, version)
            node_count += 1
            relationship_count += 1
        for item in objects:
            graph.upsert_reference_object(
                identifier,
                {
                    **item,
                    "description": reference_description(item),
                    "version": version,
                    "source_element_id": (
                        item.get("source_id") or item.get("table_id")
                        or item.get("figure_id") or item["object_id"]
                    ),
                    "bounding_regions": item.get("bounding_regions", []),
                    "content_hash": hashlib.sha256(item["text"].encode()).hexdigest(),
                    "source_provenance": "azure_document_intelligence" if extracted.get("method") == "document_intelligence" else "saved_local_extraction",
                    "link_status": "reference_only_unsaved" if temporary else "linked",
                },
            )
            node_count += 1
            relationship_count += 1
        section_map = {
            obj.get("source_id"): obj["object_id"]
            for obj in objects if obj["object_type"] == "Section"
        }
        for obj in objects:
            if obj["object_type"] == "Chunk" and obj.get("section_id") in section_map:
                graph.link_reference_objects(
                    section_map[obj["section_id"]], obj["object_id"], "HAS_CHUNK"
                )
                relationship_count += 1
        for section in extracted.get("sections", []):
            parent, child = section_map.get(section.get("parent_id")), section_map.get(section.get("id"))
            if parent and child and parent != child:
                graph.link_reference_objects(parent, child, "HAS_SECTION")
                relationship_count += 1
        # Azure can identify a figure even when it has no caption. Retain
        # its reference node instead of silently dropping a valid API element.
        indexed_figure_ids = {
            obj.get("figure_id") for obj in objects if obj["object_type"] == "Figure"
        }
        for figure in extracted.get("figures", []):
            if figure.get("id") in indexed_figure_ids:
                continue
            figure_id = "FIG" + hashlib.sha256(
                f"{identifier}:{version}:{figure.get('id')}:{figure.get('page')}".encode()
            ).hexdigest()[:24]
            graph.upsert_reference_object(
                identifier,
                {
                    "object_id": figure_id, "object_type": "Figure",
                    "page": figure.get("page"), "position": figure.get("position"),
                    "source_element_id": str(figure.get("id") or ""),
                    "bounding_regions": figure.get("bounding_regions", []),
                    "description": (
                        f"{document_label} — figure: "
                        f"{str(figure.get('caption') or 'reference')[:100]}"
                    ),
                    "version": version,
                    "content_hash": hashlib.sha256(
                        str(figure.get("caption") or "").encode()
                    ).hexdigest(),
                    "source_provenance": (
                        "azure_document_intelligence"
                        if extracted.get("method") == "document_intelligence"
                        else "saved_local_extraction"
                    ),
                    "link_status": "reference_only_unsaved" if temporary else "linked",
                },
            )
            node_count += 1
            relationship_count += 1
        active_section_id = None
        for paragraph in paragraphs:
            if paragraph.get("role") in {"title", "sectionHeading"}:
                heading_key = f"section-{paragraph.get('page')}-{paragraph.get('position')}"
                active_section_id = section_map.get(heading_key)
            source_id = str(paragraph.get("id") or "")
            paragraph_id = "PAR" + hashlib.sha256(
                f"{identifier}:{version}:{source_id}:{paragraph.get('page')}:{paragraph.get('position')}".encode()
            ).hexdigest()[:24]
            graph.upsert_reference_object(
                identifier,
                {
                    "object_id": paragraph_id, "object_type": "Paragraph",
                    "page": paragraph.get("page"), "position": paragraph.get("position"),
                    "source_element_id": source_id,
                    "bounding_regions": paragraph.get("bounding_regions", []),
                    "azure_role": paragraph.get("role"),
                    "span_offset": (paragraph.get("spans") or [{}])[0].get("offset"),
                    "span_length": (paragraph.get("spans") or [{}])[0].get("length"),
                    "description": (
                        f"{document_label} — {paragraph.get('role')}: "
                        f"{str(paragraph.get('text') or '')[:100]}"
                        if paragraph.get("role") in {"title", "sectionHeading"}
                        else f"{document_label} — paragraph reference on page {paragraph.get('page')}"
                    ),
                    "version": version, "content_hash": hashlib.sha256(
                        str(paragraph.get("text") or "").encode()
                    ).hexdigest(),
                    "source_provenance": "azure_document_intelligence" if extracted.get("method") == "document_intelligence" else "saved_local_extraction",
                    "link_status": "reference_only_unsaved" if temporary else "linked",
                },
            )
            node_count += 1
            relationship_count += 1
            if active_section_id:
                graph.link_reference_objects(active_section_id, paragraph_id, "HAS_PARAGRAPH")
                relationship_count += 1
        table_objects = {}
        for obj in objects:
            if obj["object_type"] == "Table":
                table_objects.setdefault(obj.get("table_id"), []).append(obj["object_id"])
        for table, cell in cells:
            cell_id = "CELL" + hashlib.sha256(
                f"{identifier}:{version}:{table.get('id')}:{cell.get('row')}:{cell.get('column')}".encode()
            ).hexdigest()[:24]
            graph.upsert_reference_object(
                identifier,
                {
                    "object_id": cell_id, "object_type": "TableCell",
                    "page": table.get("page"), "source_element_id": str(table.get("id")),
                    "bounding_regions": cell.get("bounding_regions", []),
                    "span_offset": (cell.get("spans") or [{}])[0].get("offset"),
                    "span_length": (cell.get("spans") or [{}])[0].get("length"),
                    "table_row": cell.get("row"), "table_column": cell.get("column"),
                    "position": cell.get("row", 0) * 100000 + cell.get("column", 0),
                    "description": (
                        f"{document_label} — table cell row {cell.get('row')} "
                        f"column {cell.get('column')} on page {table.get('page')}"
                    ),
                    "version": version,
                    "content_hash": hashlib.sha256(str(cell.get("text") or "").encode()).hexdigest(),
                    "source_provenance": "azure_document_intelligence" if extracted.get("method") == "document_intelligence" else "saved_local_extraction",
                    "link_status": "reference_only_unsaved" if temporary else "linked",
                },
            )
            node_count += 1
            relationship_count += 1
            for table_id in table_objects.get(table.get("id"), []):
                graph.link_reference_objects(table_id, cell_id, "HAS_CELL")
                relationship_count += 1
        return {"nodes": node_count, "relationships": relationship_count}

    def _link_structured(self, identifier, state):
        """Publish reference-only structure with explicitly separate writer credentials."""
        import json

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
            graph_counts = self._publish_reference_objects(
                graph, identifier, state.get("title"), extraction,
                temporary=False, version=state.get("version", 1),
            )
            return self._set_graph_status(
                identifier, state, "ready", graph_counts=graph_counts
            )
        except Exception as exc:
            return self._set_graph_status(
                identifier, state, "graph_pending", graph_error=type(exc).__name__
            )
        finally:
            if driver:
                driver.close()

    def _set_graph_status(
        self, identifier, state, status, graph_counts=None, graph_error=None
    ):
        row = self.records.read("document", identifier)
        semantic_status = state.get("semantic_graph_status", "pending")
        if self.settings.semantic_graph_ingestion_enabled and semantic_status == "disabled":
            # Enabling semantic ingestion after a document was structurally linked
            # must make the existing durable job eligible for the background worker.
            semantic_status = "pending"
        updated = {
            **state,
            "status": status,
            "phase": "complete" if status == "ready" else "link",
            "local_index_ready": bool(state.get("chunks")),
            "graph_link_status": "linked" if status == "ready" else "pending",
            "semantic_graph_status": (
                semantic_status
                if status == "ready" and self.settings.semantic_graph_ingestion_enabled
                else state.get("semantic_graph_status", "disabled")
            ),
        }
        if graph_counts:
            updated.update(
                graph_nodes=graph_counts["nodes"],
                graph_relationships=graph_counts["relationships"],
            )
        if status == "graph_pending":
            import time

            attempts = int(state.get("graph_retry_attempts", 0)) + 1
            updated.update(
                graph_retry_attempts=attempts,
                graph_retry_after=time.time() + min(300, 10 * (2 ** (attempts - 1))),
                graph_last_error=graph_error or "GraphUnavailable",
                graph_retry_terminal=attempts >= 5,
            )
        else:
            for key in (
                "graph_retry_attempts", "graph_retry_after", "graph_last_error",
                "graph_retry_terminal",
            ):
                updated.pop(key, None)
        self.records.write("document", identifier, updated, previous=row, fields={"status": status})
        return updated
