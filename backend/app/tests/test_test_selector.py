import pytest

from scripts.select_tests import select

pytestmark = pytest.mark.unit


def test_selector_maps_memory_and_frontend_changes_conservatively():
    result = select([
        "backend/app/services/semantic_memory.py",
        "frontend/src/App.tsx",
    ])
    assert "backend/app/tests/test_semantic_memory.py" in result["backend"]
    assert "backend/app/tests/test_graph_repo.py" in result["backend"]
    assert result["frontend"] == ["src/App.test.tsx"]
    assert "backend/app/services/semantic_memory.py" in result["python"]
    assert result["typecheck"] is True
    assert result["e2e"] is False


def test_selector_falls_back_to_backend_regression_for_unknown_module():
    result = select(["backend/app/new_shared_contract.py"])
    assert result["backend"] == ["backend/app/tests"]


def test_selector_reserves_browser_for_browser_contract_changes():
    result = select(["frontend/tests/e2e.spec.ts"])
    assert result["e2e"] is True
