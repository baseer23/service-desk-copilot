from backend.app.services.entities import extract_entities


def test_extract_entities_simple():
    chunks = [
        {"text": "Widget Alpha connects to Widget Beta."},
        {"text": "Ensure Alpha safety before Beta operations."},
    ]
    entities = extract_entities(chunks)
    assert "widget alpha" in entities
    assert "widget beta" in entities
    assert len(entities) >= 2
