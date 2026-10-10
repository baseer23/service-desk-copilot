"""Exact codes must survive semantic ranking and broad graph entity filtering."""

import pytest


@pytest.mark.parametrize("code", ["SUPPORT54007", "support54007"])
def test_exact_code_returns_its_definition_through_real_mcp(make_client, code):
    client = make_client()
    harness = client.app.state.agent.harness
    texts = [
        f"Vendor reference code: OTHER{index:05}. Generic authentication guidance unrelated to this code."
        for index in range(12)
    ]
    texts += [
        "Vendor reference code: SUPPORT540070. This is a different code and must not be substituted.",
        "Vendor reference code: SUPPORT54007. The documented meaning is a missing client certificate; check the approved certificate enrollment procedure.",
    ]
    for index, text in enumerate(texts):
        client.portal.call(
            harness.invoke,
            "ingest_text",
            {
                "title": f"Fictional code reference {index}",
                "text": text,
                "source_type": "local_fixture",
                "source": "Fictional code reference",
            },
        )
    result = client.portal.call(
        harness.invoke,
        "search_knowledge",
        {"question": f"Explain vendor reference code {code}", "mode": "HYBRID", "top_k": 1},
    )
    assert result["status"] == "ok" and len(result["items"]) == 1
    hit = result["items"][0]
    assert "missing client certificate" in hit["text"]
    assert "SUPPORT540070" not in hit["text"]
    assert hit["metadata"]["doc_id"] and hit["source_system"] == "local_fixture"
