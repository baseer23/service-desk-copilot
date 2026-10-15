"""Local originals, text extraction, Chroma retrieval and transactional jobs."""

from backend.app.services.document_repository import DocumentRepository
from backend.app.services.capabilities import knowledge_stores
from backend.app.store.local_knowledge import LocalDocuments
from backend.app.store.local_records import LocalRecords


class LocalDocumentExtractor:
    def __init__(self, settings, records):
        self.settings, self.records = settings, records

    def extract(self, data, media_type):
        # Native extraction avoids uploading ordinary PDFs to any cloud service.
        if media_type == "application/pdf":
            from backend.app.services.local_pdf import extract_pdf_pages
            pages = extract_pdf_pages(data)
            if pages and all(page["text"].strip() for page in pages):
                return {"pages": pages, "tables": [], "method": "pdf_text"}
        if not self.settings.azure_document_endpoint:
            raise ValueError("Document processing is not configured for scanned or image documents")
        from backend.app.services.document_processing import AzureDocumentExtractor

        extractor = AzureDocumentExtractor(self.settings, self.records)
        try:
            return extractor.extract(data, media_type)
        finally:
            extractor.client.close()
            if extractor.credential:
                extractor.credential.close()


class LocalDocumentRepository(DocumentRepository):
    def __init__(self, settings, records=None, *, extractor=None, embeddings=None):
        records = records or LocalRecords(settings)
        vector, _, _ = knowledge_stores(settings, connect_graph=False, records=records)
        super().__init__(
            settings, records=records,
            blobs=LocalDocuments(settings, records), search=vector,
            extractor=extractor or LocalDocumentExtractor(settings, records),
            embeddings=embeddings,
        )

    def process(self, identifier):
        state = super().process(identifier)
        if not self.settings.neo4j_uri:
            return state
        from backend.app.services.knowledge_policy import is_test_knowledge
        if not self.settings.test_mode and is_test_knowledge(state):
            return state
        # Local indexing is already committed. Optional Aura enrichment cannot
        # invalidate a successful upload when the online graph is unavailable.
        vector, graph, driver = knowledge_stores(self.settings, records=self.records)
        try:
            if driver is None:
                return state
            from backend.app.services.entities import extract_entities
            import json
            graph.upsert_document(identifier, state.get("title"), "local_document")
            offset = 0
            while True:
                rows = vector.vector._collection.get(
                    where={"$and": [{"tenant_id": self.records.tenant()}, {"doc_id": identifier}]},
                    include=["documents", "metadatas"], limit=200, offset=offset,
                )
                for index, physical_id in enumerate(rows["ids"]):
                    stored = rows["metadatas"][index]
                    metadata = json.loads(stored["metadata_json"])
                    chunk_id, text = stored.get("chunk_id", physical_id), rows["documents"][index]
                    graph.upsert_chunk(identifier, chunk_id, int(metadata.get("ord", index + offset)), text, max(1, len(text) // 4))
                    graph.link_doc_chunk(identifier, chunk_id)
                    for entity in extract_entities([{"text": text}]):
                        graph.link_chunk_entity(chunk_id, graph.upsert_entity(entity))
                if len(rows["ids"]) < 200:
                    break
                offset += 200
            return state
        finally:
            if driver:
                driver.close()
