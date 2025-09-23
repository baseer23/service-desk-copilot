from types import SimpleNamespace

from backend.app.rag.planner import Planner


class FakeGraphRepo:
    def __init__(self, degrees):
        self.degrees = degrees

    def get_entity_degrees(self, names):
        return {name: self.degrees.get(name, 0) for name in names}


def test_planner_selects_graph_mode():
    graph_repo = FakeGraphRepo({"widget alpha": 5})
    planner = Planner(settings=SimpleNamespace(top_k=4), graph_repo=graph_repo)
    plan = planner.plan("Explain Widget Alpha operations")
    assert plan["mode"] == "GRAPH"
    assert plan["top_k"] == 4


def test_planner_selects_vector_mode_when_no_entities():
    graph_repo = FakeGraphRepo({})
    planner = Planner(settings=SimpleNamespace(top_k=3), graph_repo=graph_repo)
    plan = planner.plan("How do I reset passwords?")
    assert plan["mode"] == "VECTOR"


def test_planner_selects_hybrid_with_mixed_entities():
    graph_repo = FakeGraphRepo({"widget beta": 1})
    planner = Planner(settings=SimpleNamespace(top_k=5), graph_repo=graph_repo)
    plan = planner.plan("What is Widget Beta and how to use it?")
    assert plan["mode"] == "HYBRID"
