import pytest

from backend.app.core.principal import Principal, principal_scope
from backend.app.services.semantic_memory import SemanticMemoryService
from backend.app.store.local_records import LocalRecords

pytestmark = [pytest.mark.memory, pytest.mark.integration]


class GraphSink:
    available = True

    def __init__(self):
        self.batches = []

    def upsert_semantic_memory(self, batch):
        self.batches.append(batch)


def service(settings, tmp_path, graph=None):
    configured = settings.model_copy(
        update={"case_db_path": tmp_path / "semantic.db", "local_persistence_enabled": True}
    )
    return SemanticMemoryService(configured, LocalRecords(configured), graph), configured


def test_explicit_name_is_durable_idempotent_and_graph_linked(settings, tmp_path):
    sink = GraphSink()
    memory, configured = service(settings, tmp_path, sink)
    first = memory.capture_conversation("My name is Abdul Khan.", "conversation-1")
    assert first["captured"] and first["graph_status"] == "linked"
    assert memory.profile()["name"] == "Abdul Khan"
    assert not memory.capture_conversation("My name is Abdul Khan.", "conversation-2")["captured"]
    restarted = SemanticMemoryService(configured, LocalRecords(configured))
    assert restarted.profile()["name"] == "Abdul Khan"
    assert len(sink.batches) == 1
    assert memory.forget_profile() == 1
    assert sink.batches[-1].facts[0].status == "revoked"


def test_correction_supersedes_without_deleting_provenance(settings, tmp_path):
    memory, _ = service(settings, tmp_path)
    original = memory.capture_conversation("My name is Old Name.", "one")["fact"]
    corrected = memory.capture_conversation("No, my name is New Name.", "two")["fact"]
    assert memory.profile()["name"] == "New Name"
    old = memory.records.read("semantic_fact", original["id"])
    assert old["payload"]["status"] == "superseded"
    assert corrected["supersedes_id"] == original["id"]


def test_unavailable_graph_preserves_pending_job_and_local_profile(settings, tmp_path):
    memory, _ = service(settings, tmp_path)
    result = memory.capture_conversation("Call me Local User.", "offline")
    assert result["graph_status"] == "graph_pending"
    job = memory.records.read("semantic_graph_job", result["fact"]["id"])
    assert job["payload"]["status"] == "pending" and job["payload"]["attempts"] == 0
    assert memory.profile()["name"] == "Local User"


def test_owner_isolation_opt_out_unrelated_text_and_forget(settings, tmp_path):
    memory, _ = service(settings, tmp_path)
    alice = Principal("tenant-a", "alice")
    bob = Principal("tenant-a", "bob")
    with principal_scope(alice):
        memory.capture_conversation("My name is Alice Example.", "alice-chat")
        assert not memory.capture_conversation(
            "My name is Not Saved.", "private", allow_personal_memory=False
        )["captured"]
        assert not memory.capture_conversation(
            "The document says that her name is Document Person.", "document-chat"
        )["captured"]
    with principal_scope(bob):
        assert memory.profile()["name"] is None
        memory.capture_conversation("My name is Bob Example.", "bob-chat")
        assert memory.profile()["name"] == "Bob Example"
    with principal_scope(alice):
        assert memory.profile()["name"] == "Alice Example"
        assert memory.forget_profile() == 1
        assert memory.profile()["name"] is None


def test_http_conversations_capture_and_recall_profile(make_client):
    client = make_client({"LOCAL_PERSISTENCE_ENABLED": "true"})
    response = client.post("/ask", json={"summary": "My name is API User."})
    assert response.status_code == 200
    profile = client.get("/memory/profile")
    assert profile.status_code == 200
    assert profile.json()["name"] == "API User"
    forgotten = client.delete("/memory/profile")
    assert forgotten.status_code == 200 and forgotten.json()["revoked"] == 1
    assert client.get("/memory/profile").json()["name"] is None


def test_planner_routes_profile_questions_without_unrelated_memory_reads():
    from backend.app.models.instructions import stage_instructions

    instructions = stage_instructions("plan", {"workflow": "knowledge"})
    assert "get_authenticated_profile" in instructions
    assert "unrelated calculations" in instructions


def test_general_explicit_memory_semantic_recall_correction_and_idempotency(settings, tmp_path):
    sink = GraphSink()
    memory, configured = service(settings, tmp_path, sink)
    first = memory.capture_conversation(
        "Remember that I prefer Azure for deployments.", "cloud-one"
    )
    assert first["fact"]["category"] == "preference"
    assert first["fact"]["predicate"] == "PREFERRED_CLOUD"
    assert first["fact"]["value"] == "Azure"
    assert first["vector_status"] == "indexed"
    duplicate = memory.capture_conversation("Please remember I prefer Azure.", "cloud-two")
    assert not duplicate["captured"] and duplicate["reason"] == "duplicate"
    assert len([fact for fact in memory.profile()["facts"] if fact["predicate"] == "PREFERRED_CLOUD"]) == 1
    assert memory.vector.count() == 1

    restarted = SemanticMemoryService(configured, LocalRecords(configured), sink)
    recalled = restarted.search("Which cloud platform do I like to deploy to?")
    assert recalled["facts"][0]["value"] == "Azure"
    corrected = restarted.capture_conversation(
        "Actually, remember that I prefer AWS instead.", "cloud-three"
    )
    assert corrected["fact"]["value"] == "AWS"
    old = restarted.records.read("semantic_fact", first["fact"]["id"])
    assert old["payload"]["status"] == "superseded"
    assert restarted.search("What is my preferred cloud?")["facts"][0]["value"] == "AWS"


def test_generic_notes_relationships_and_single_fact_forgetting(settings, tmp_path):
    memory, _ = service(settings, tmp_path, GraphSink())
    note = memory.capture_conversation("Keep in mind that I work best before noon.", "note")
    assert note["fact"]["predicate"] == "PERSONAL_NOTE"
    technology = memory.capture_conversation("Remember that I use Neo4j Aura.", "tech")
    job = memory.records.read("semantic_graph_job", technology["fact"]["id"])
    assert job["payload"]["batch"]["relationships"][0]["relationship"] == "USES"
    assert memory.forget(technology["fact"]["id"])
    assert memory.search("What technology do I use?")["facts"] == []
    assert any(fact["id"] == note["fact"]["id"] for fact in memory.profile()["facts"])


def test_memory_http_api_reports_save_search_and_delete(make_client):
    client = make_client({"LOCAL_PERSISTENCE_ENABLED": "true"})
    saved = client.post(
        "/memory/save", json={"text": "Remember that DeskMate is my main project."}
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["local_status"] == "saved"
    found = client.get("/memory", params={"query": "What project am I working on?"})
    assert found.status_code == 200
    assert found.json()["facts"][0]["value"] == "DeskMate"
    deleted = client.delete(f'/memory/{body["fact"]["id"]}')
    assert deleted.status_code == 200 and deleted.json()["revoked"]
    assert client.get("/memory", params={"query": "my project"}).json()["facts"] == []


def test_offline_graph_job_reconciles_without_changing_local_fact(settings, tmp_path):
    sink = GraphSink()
    sink.available = False
    memory, _ = service(settings, tmp_path, sink)
    saved = memory.capture_conversation("Remember that I use ChromaDB.", "offline")
    assert saved["graph_status"] == "graph_pending"
    assert memory.search("Which technology do I use?")["facts"][0]["value"] == "ChromaDB"
    sink.available = True
    reconciled = memory.retry_pending()
    assert reconciled["linked"] == 1
    assert memory.records.read("semantic_graph_job", saved["fact"]["id"])["payload"]["status"] == "linked"
