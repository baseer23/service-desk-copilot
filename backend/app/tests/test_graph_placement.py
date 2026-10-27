import pytest

from backend.app.models.document_graph_models import (
    DocumentGraphProposal,
    GraphPlacementPlan,
    ProposedEntity,
)
from backend.app.services.graph_placement import GraphPlacementService


@pytest.mark.asyncio
async def test_invalid_or_missing_placement_is_conservatively_unresolved():
    proposal = DocumentGraphProposal(
        document_id="doc-1",
        entities=[
            ProposedEntity(
                temporary_id="entity-1", name="Gateway", entity_type="Technology",
                evidence_object_ids=["C1"],
            ),
            ProposedEntity(
                temporary_id="entity-2", name="Certificate", entity_type="Technology",
                evidence_object_ids=["C2"],
            ),
        ],
    )

    class Provider:
        async def generate_structured(self, *_args):
            return GraphPlacementPlan.model_validate({
                "document_id": "wrong-document",
                "placements": [{
                    "temporary_id": "entity-1", "decision": "REUSE_EXISTING",
                    "existing_entity_id": "unsupported-id", "reason": "Weak match",
                }],
            })

    vector = type("Vector", (), {
        "resolve_object_ids": lambda self, ids: [
            {"metadata": {"object_id": value}} for value in ids
        ]
    })()
    service = GraphPlacementService(Provider(), object(), vector, object())
    plan = await service.prepare_placement(
        proposal,
        discovery={
            "entity-1": {"candidates": [{"id": "supported-id"}], "neighborhoods": []},
            "entity-2": {"candidates": [], "neighborhoods": []},
        },
    )
    assert plan.document_id == "doc-1"
    assert [item.temporary_id for item in plan.placements] == ["entity-1", "entity-2"]
    assert all(item.decision == "UNRESOLVED" for item in plan.placements)
    assert all(item.existing_entity_id is None for item in plan.placements)
