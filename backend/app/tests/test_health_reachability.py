def test_health_describes_optional_aura(make_client):
    client = make_client()
    body = client.get("/health").json()
    assert body["aura_configured"] is False
    assert body["aura_connected"] is False
    assert body["test_mode"] is True
