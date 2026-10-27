"""One opt-in live acceptance path: PDF upload -> Aura/Chroma -> real chat."""

import io
import json
import os
import time
import uuid

import httpx
from neo4j import GraphDatabase

from backend.app.core.config import Settings
from backend.app.core.principal import Principal, principal_scope
from backend.app.services.local_documents import LocalDocumentRepository
from backend.app.store.local_knowledge import LocalDocuments
from backend.app.store.local_records import LocalRecords


def _pdf(text: str) -> bytes:
    """Create a one-page text PDF without adding a test-only dependency."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    lines = escaped.splitlines()
    commands = ["BT", "/F1 11 Tf", "72 730 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -22 Td")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(output.tell())
        output.write(f"{index} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode())
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode())
    output.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return output.getvalue()


def test_document_upload_to_neo4j_to_chat():
    settings = Settings()
    base_url = os.getenv("DESKMATE_E2E_BASE_URL", "http://127.0.0.1:8000")
    run_id = os.getenv("DESKMATE_E2E_RUN_ID") or uuid.uuid4().hex[:12]
    title = f"DeskMate GraphRAG Integration Verification {run_id}"
    text = "\n".join([
        title,
        "The fictional company WillowBay Robotics owns Project Starling.",
        "Project Starling uses the HelioSwitch platform for network monitoring.",
        "The HelioSwitch platform is maintained by engineer Morgan Vale.",
        "WillowBay Robotics operates Project Starling from its North Laboratory.",
        f"Unique test-run identifier: {run_id}.",
    ])
    tenant = settings.auth_allowed_tenants[0] if settings.auth_allowed_tenants else settings.voice_tenant_id

    with httpx.Client(base_url=base_url, timeout=120) as client:
        ready = client.get("/readiness")
        ready.raise_for_status()
        readiness = ready.json()
        assert readiness["azure_document_intelligence_available"], readiness
        assert readiness["structured_ingestion_available"], readiness
        existing = next(
            (item for item in client.get("/documents").json() if item.get("title") == title),
            None,
        )
        if existing:
            document_id = existing["id"]
        else:
            uploaded = client.post(
                "/documents/upload",
                files={"file": (f"graphrag-{run_id}.pdf", _pdf(text), "application/pdf")},
                data={
                    "title": title,
                    "ingestion_mode": "structured_graph",
                    "document_processing": "azure_di",
                },
            )
            uploaded.raise_for_status()
            document_id = uploaded.json().get("id")
        assert document_id

        with principal_scope(Principal(tenant, "document-graph-chat-e2e", True)):
            records = LocalRecords(settings)
            durable = records.read("document", document_id)
            assert durable and durable["payload"]["title"] == title
            assert LocalDocuments(settings, records).location(document_id, "original").is_file()

        deadline = time.monotonic() + 900
        state = {}
        while time.monotonic() < deadline:
            response = client.get(f"/documents/{document_id}/status")
            response.raise_for_status()
            state = response.json()
            print("document_status=" + json.dumps({
                key: state.get(key) for key in (
                    "status", "phase", "pages", "chunks", "extraction_method",
                    "local_index_ready", "graph_link_status", "semantic_graph_status",
                    "processing_error_code", "processing_error_reason", "failed_stage",
                    "graph_last_error", "semantic_graph_reason",
                )
            }, sort_keys=True))
            if state.get("status") == "error" or state.get("semantic_graph_status") in {"failed", "partial"}:
                raise AssertionError(state)
            if (
                state.get("status") == "ready"
                and state.get("local_index_ready") is True
                and state.get("graph_link_status") == "linked"
                and state.get("semantic_graph_status") == "linked"
            ):
                break
            time.sleep(2)
        else:
            raise AssertionError({"timeout": True, "last_state": state})

        assert state["extraction_method"] == "document_intelligence"
        assert state["pages"] >= 1 and state["chunks"] >= 1

        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_ingestion_user, settings.neo4j_ingestion_password.get_secret_value()),
            connection_timeout=10,
            max_transaction_retry_time=0,
        )
        try:
            with driver.session(database=settings.neo4j_database) as session:
                structural = session.run(
                    "MATCH (d:Document)-[:HAS_PAGE]->(p:Page) "
                    "WHERE d.tenant_id=$tenant AND d.original_id=$document "
                    "OPTIONAL MATCH (p)-[]->(o) WHERE o.tenant_id=$tenant "
                    "RETURN count(DISTINCT p) AS pages,count(DISTINCT o) AS objects",
                    tenant=tenant, document=document_id,
                ).single(strict=True)
                claims = [dict(row) for row in session.run(
                    "MATCH (c:GraphClaim)-[:DOCUMENTED_IN]->(d:Document) "
                    "WHERE d.tenant_id=$tenant AND d.original_id=$document "
                    "MATCH (c)-[:SUBJECT]->(a:Entity) MATCH (c)-[:OBJECT]->(b:Entity) "
                    "OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(o) "
                    "RETURN d.title AS title,a.name AS subject,c.predicate AS predicate,"
                    "b.name AS object,collect(DISTINCT o.original_id) AS object_ids",
                    tenant=tenant, document=document_id,
                )]
        finally:
            driver.close()
        assert structural["pages"] >= 1 and structural["objects"] >= 1, dict(structural)
        supported = [row for row in claims if [value for value in row["object_ids"] if value]]
        assert supported, claims
        assert any(
            "willowbay robotics" in row["subject"].casefold()
            and "project starling" in row["object"].casefold()
            or "project starling" in row["subject"].casefold()
            and "helioswitch" in row["object"].casefold()
            for row in supported
        ), supported

        object_id = next(value for row in supported for value in row["object_ids"] if value)
        with principal_scope(Principal(tenant, "document-graph-chat-e2e", True)):
            hydrated = LocalDocumentRepository(settings).search_store.resolve_object_ids([object_id])
        assert hydrated, {"unresolved_object_id": object_id}
        passage = hydrated[0]
        assert passage["metadata"]["doc_id"] == document_id
        assert passage["metadata"]["title"] == title
        assert passage["metadata"].get("page") == 1
        assert "Project Starling" in passage["text"]

        chat = client.post("/ask", json={
            "summary": (
                "According to my uploaded DeskMate GraphRAG Integration Verification "
                "document, which company owns Project Starling?"
            )
        }, timeout=300)
        chat.raise_for_status()
        answer = chat.json()
        result = answer["result"]
        final_text = str(result.get("final_answer") or (result.get("final_response") or {}).get("answer") or "")
        assert answer["intent"] == "knowledge_question"
        assert result.get("explicit_saved_knowledge") is True
        executed = "\n".join(map(str, result.get("executed_tool_calls", [])))
        events = json.dumps(answer.get("events", []))
        assert "search_knowledge" in executed
        assert "neo4j__read-cypher" in executed or "search_graph" in executed
        assert "WillowBay Robotics" in final_text, final_text
        evidence = json.dumps(result.get("evidence", []))
        assert title in evidence and "Project Starling" in evidence
        assert '"graph_enrichment": "available"' in evidence
        assert "search_knowledge" in events
        assert "neo4j" in events.casefold() or "graph" in events.casefold()
        print("chat_answer=" + final_text)
