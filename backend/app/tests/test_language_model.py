"""Sanity checks for language model outputs."""

from __future__ import annotations


def test_language_model_returns_text(make_client, tmp_path):
    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
        }
    )

    try:
        response = client.post("/ask", json={"question": "Is anyone there?"})
    finally:
        client.close()

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body.get("answer"), str)
    assert body["answer"].strip() != ""
