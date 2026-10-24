"""Website registration control-plane behavior; no public network is used."""

from backend.app.core.principal import Principal, principal_scope
from backend.app.services.websites import WebsiteRepository
from backend.app.store.local_records import LocalRecords


def repository(settings, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.web.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    return WebsiteRepository(LocalRecords(settings))


def test_registration_is_idempotent_and_does_not_claim_monitoring(settings, monkeypatch):
    repo = repository(settings, monkeypatch)
    with principal_scope(Principal("tenant-a", "owner-a")):
        first = repo.register("https://EXAMPLE.com:443/docs/")
        second = repo.register("https://example.com/docs")

    assert first["id"] == second["id"]
    assert second["duplicate"] is True
    assert first["monitoring_active"] is False
    assert first["crawl_status"] == "not_started"


def test_registration_is_tenant_scoped(settings, monkeypatch):
    repo = repository(settings, monkeypatch)
    with principal_scope(Principal("tenant-a", "owner-a")):
        first = repo.register("https://example.com/docs")
    with principal_scope(Principal("tenant-b", "owner-b")):
        second = repo.register("https://example.com/docs")
        assert repo.list() == [{key: value for key, value in second.items() if key != "duplicate"}]

    assert first["id"] != second["id"]


def test_pause_and_resume_preserve_honest_inactive_status(settings, monkeypatch):
    repo = repository(settings, monkeypatch)
    with principal_scope(Principal("tenant-a", "owner-a")):
        website = repo.register("https://example.com/docs")
        assert repo.set_paused(website["id"], True)["status"] == "paused"
        resumed = repo.set_paused(website["id"], False)

    assert resumed["status"] == "registered"
    assert resumed["monitoring_active"] is False
