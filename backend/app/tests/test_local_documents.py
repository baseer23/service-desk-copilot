import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from backend.app.agent.models import InvestigationPlan, ToolRequest
from backend.app.agent.tools import AgentToolHarness
from backend.app.mcp.client import DeskMateMCPClient
from backend.app.repositories.documents import DocumentRepository
from backend.app.services.document_extract import DocumentError, extract_document
from backend.app.tests.test_ingest_pdf_guard import MINIMAL_PDF


def test_real_pdf_text_and_utf8_extraction():
    result = extract_document(MINIMAL_PDF, "application/pdf")
    assert "Hello PDF" in result["text"] and result["pages"] == 1
    assert result["method"] == "pdf_text"
    assert extract_document(b"Fictional VPN guide", "text/plain")["method"] == "text"
    with pytest.raises(DocumentError, match="valid PDF"):
        extract_document(b"Not a PDF", "application/pdf")
    with pytest.raises(DocumentError, match="UTF-8"):
        extract_document(b"\xff", "text/plain")
    with pytest.raises(DocumentError, match="no text"):
        extract_document(b"   ", "text/plain")


def test_blank_pdf_reports_ocr_dependency(monkeypatch):
    from backend.app.services.document_extract import _pdf_worker

    monkeypatch.setattr("backend.app.services.document_extract.ocr_available", lambda: False)
    with pytest.raises(DocumentError, match="Local OCR requires"):
        _pdf_worker(MINIMAL_PDF.replace(b"Hello PDF", b"         "))


def test_pdf_timeout_is_actionable(monkeypatch):
    import subprocess

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("private parser", 35)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(DocumentError, match="timed out"):
        extract_document(MINIMAL_PDF, "application/pdf")


def test_local_store_restart_dedup_concurrency_and_case_preservation(tmp_path):
    from backend.app.agent.memory import CaseMemoryStore

    path = tmp_path / "cases.db"
    memory = CaseMemoryStore(path)
    memory.save_case("preserved", {"case_id": "preserved", "ticket": {"summary": "Keep this case"}})
    repo = DocumentRepository(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: repo.save("Fictional VPN authentication guide", "VPN guide", "text"),
                range(4),
            )
        )
    assert sum(not result["duplicate"] for result in results) == 1
    restarted = DocumentRepository(path)
    assert len(restarted.list_documents()) == 1
    assert restarted.search('VPN OR " --', 6)[0]["metadata"]["title"] == "VPN guide"
    assert memory.load_case("preserved")["ticket"]["summary"] == "Keep this case"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM knowledge_search").fetchone()[0] == 1


def test_local_http_mcp_paths_do_not_depend_on_retired_financial_controls(make_client):
    client = make_client(
        {
            "TEST_MODE": "true",
            "LOCAL_PERSISTENCE_ENABLED": "true",
            "GENERATION_PROVIDER": "azure_openai",
            "EMBED_PROVIDER": "stub",
            "AZURE_FOUNDRY_ENDPOINT": "https://example.openai.azure.com",
            "AZURE_MODEL_DEPLOYMENT": "test-reasoning",
            "AZURE_USE_ENTRA_ID": "true",
        }
    )
    assert client.get("/readiness").json()["local_documents_ready"]
    assert client.get("/readiness").json()["semantic_ingestion_ready"]
    upload = {"file": ("fixture.pdf", MINIMAL_PDF, "application/pdf")}
    assert "Hello PDF" in client.post("/documents/extract", files=upload).json()["text"]
    saved = client.post("/documents/upload", files=upload, data={"title": "Isolated PDF policy"})
    assert saved.status_code == 200 and saved.json()["storage"] == "local"
    assert client.post("/documents/upload", files=upload).json()["duplicate"]
    indexed = client.post(f"/documents/{saved.json()['id']}/index")
    assert indexed.status_code == 200 and indexed.json()["chunks"] > 0
    found = client.get("/knowledge/search", params={"question": "Hello PDF"}).json()
    assert not found["generated_answer"] and found["mode"] == "keyword"
    assert found["items"][0]["metadata"]["title"] == "Isolated PDF policy"
    # An explicitly selected Azure provider is attempted; no local allowance gate
    # short-circuits the request before normal upstream error handling.
    assert client.post("/ask", json={"summary": "Fictional VPN failure"}).status_code == 502
    case = client.post("/cases", json={"summary": "Fictional VPN failure"}).json()
    assert case["status"] == "new" and not case["final_answer"]
    assert client.get(f"/cases/{case['case_id']}").status_code == 200
    assert "azure_usage" not in client.get("/admin/readiness").json()
    bad = client.post(
        "/documents/extract", files={"file": ("bad.pdf", b"%PDF-invalid", "application/pdf")}
    )
    assert bad.status_code == 422 and "parsed" in bad.json()["detail"]


def test_local_save_semantic_index_real_chroma_dedup_and_retrieval(make_client):
    client = make_client()
    result = client.post(
        "/documents/upload", files={"file": ("fixture.pdf", MINIMAL_PDF, "application/pdf")}
    )
    identifier = result.json()["id"]
    indexed = client.post(f"/documents/{identifier}/index")
    assert indexed.status_code == 200 and indexed.json()["vector_count"] > 0
    assert client.post(f"/documents/{identifier}/index").json()["duplicate"]
    assert client.get("/knowledge/search", params={"question": "Hello PDF"}).json()["items"]
    client.post(
        "/ingest/paste",
        json={"text": "UniqueOfflineLookupTag: fictional printer guide", "title": "Indexed only"},
    )
    found = client.get("/knowledge/search", params={"question": "UniqueOfflineLookupTag"}).json()[
        "items"
    ]
    assert found[0]["storage"] == "chroma" and found[0]["metadata"]["title"] == "Indexed only"


def test_structured_upload_mode_is_explicit_persisted_and_nonretroactive(make_client):
    client = make_client(
        {
            "__GRAPH_WRITER_AVAILABLE": True,
            "NEO4J_URI": "neo4j+s://fictional.example",
            "NEO4J_INGESTION_ENABLED": "true",
            "NEO4J_INGESTION_USER": "isolated-writer",
            "NEO4J_INGESTION_PASSWORD": "isolated-secret",
            "LOCAL_PERSISTENCE_ENABLED": "true",
        }
    )
    assert client.get("/readiness").json()["structured_ingestion_available"] is True
    files = {"file": ("structured.pdf", MINIMAL_PDF, "application/pdf")}
    uploaded = client.post(
        "/documents/upload",
        files=files,
        data={"title": "Structured PDF", "ingestion_mode": "structured_graph"},
    ).json()
    assert uploaded["ingestion_mode"] == "structured_graph"
    indexed = client.post(f"/documents/{uploaded['id']}/index").json()
    assert indexed["status"] == "ready" and indexed["ingestion_mode"] == "structured_graph"
    duplicate = client.post(
        "/documents/upload",
        files=files,
        data={"ingestion_mode": "standard"},
    )
    assert duplicate.status_code == 400
    assert "cannot be disabled" in duplicate.json()["detail"]


def test_automatic_ingestion_accepts_plain_text_with_local_fallback(
    make_client, monkeypatch
):
    import backend.app.services.local_documents as local_documents

    monkeypatch.setattr(
        local_documents,
        "graph_writer_readiness",
        lambda _settings: {"available": True, "blocker": None},
    )
    client = make_client(
        {
            "AUTO_DOCUMENT_GRAPH_INGESTION": "true",
            "LOCAL_PERSISTENCE_ENABLED": "true",
            "AZURE_DOCUMENT_ENDPOINT": None,
            "NEO4J_URI": "neo4j+s://fictional.example",
            "NEO4J_INGESTION_ENABLED": "true",
            "NEO4J_INGESTION_USER": "isolated-writer",
            "NEO4J_INGESTION_PASSWORD": "isolated-secret",
        }
    )
    response = client.post(
        "/documents/upload",
        files={"file": ("notes.txt", b"Local fallback content", "text/plain")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ingestion_mode"] == "structured_graph"
    assert body["document_processing"] == "local"


def test_simultaneous_local_cases_remain_separate(make_client):
    client = make_client()
    summaries = [
        "Fictional MFA failure",
        "Fictional VPN authentication failure",
        "Fictional printer offline",
        "Fictional Outlook not opening",
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda summary: client.post("/cases", json={"summary": summary}), summaries)
        )
    identifiers = [response.json()["case_id"] for response in responses]
    assert len(set(identifiers)) == 4
    for identifier, summary in zip(identifiers, summaries, strict=True):
        saved = client.get(f"/cases/{identifier}").json()
        assert saved["ticket"]["summary"] == summary and saved["status"] == "new"


@pytest.mark.asyncio
async def test_staging_writes_cannot_be_model_planned_and_keyword_reads_use_mcp(settings):
    client = DeskMateMCPClient(settings)
    try:
        harness = AgentToolHarness(client)
        await harness.initialize()
        for name, arguments in [
            ("save_document", {"data_base64": "YWJj"}),
            ("index_document", {"document_id": "untrusted"}),
        ]:
            with pytest.raises(ValueError, match="not permitted"):
                harness.validate_plan(
                    InvestigationPlan(
                        goal="Attempt write",
                        stop_condition="Stop",
                        tool_calls=[ToolRequest(name=name, arguments=arguments)],
                    ),
                    "service_desk",
                )
        result = await client.call_tool("search_knowledge", {"question": "VPN", "mode": "KEYWORD"})
        assert result["status"] == "ok"
    finally:
        await client.close()
