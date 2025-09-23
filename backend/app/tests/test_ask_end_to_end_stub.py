"""End-to-end test through ingest and ask with stub provider."""

from __future__ import annotations

from backend.app.core.config import DEFAULT_STUB_ANSWER


def test_ingest_and_ask_cycle(make_client, tmp_path):
    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
        }
    )

    try:
        ingest_resp = client.post(
            "/ingest/paste",
            json={
                "title": "Runbook",
                "text": "Alpha systems are patched weekly. Use ticket ABC-123 for escalations.",
            },
        )
        assert ingest_resp.status_code == 200
        ingest_body = ingest_resp.json()
        assert ingest_body["chunks_ingested"] > 0

        ask_resp = client.post("/ask", json={"question": "Where do Alpha escalations go?"})
        assert ask_resp.status_code == 200
        body = ask_resp.json()
    finally:
        client.close()

    assert body["answer"] == DEFAULT_STUB_ANSWER
    assert body["provider"] == "stub"
    assert body["latency_ms"] >= 0
    assert 0 <= body["confidence"] <= 1
    assert body["planner"]["mode"]
    assert len(body["citations"]) >= 1
