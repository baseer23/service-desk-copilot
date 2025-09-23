from types import SimpleNamespace

from backend.app.rag.answer import Responder
from backend.app.models.dto import Citation


class DummyProvider:
    def __init__(self, text="Generated answer"):
        self.text = text

    def generate(self, prompt):
        return self.text


sample_chunks = [
    {"id": "chunk-1", "text": "Widget Alpha connects to Beta.", "metadata": {"doc_id": "doc-1", "title": "Manual"}, "score": 0.2},
]


def test_stub_responder_includes_citations():
    settings = SimpleNamespace(model_provider="stub")
    responder = Responder(settings=settings, provider=DummyProvider("LLM answer"))
    planner = {"mode": "VECTOR", "reasons": [], "top_k": 3}
    response = responder.answer("How to connect Alpha?", planner, sample_chunks)
    assert response.answer
    assert response.citations[0].doc_id == "doc-1"
    assert response.citations[0].snippet is not None
    assert response.provider == "stub"
