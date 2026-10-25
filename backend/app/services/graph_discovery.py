"""Bounded tenant-scoped discovery for proposed document entities."""


class GraphDiscovery:
    def __init__(self, graph):
        self.graph = graph

    def discover(self, proposal):
        discovered = {}
        for entity in proposal.entities:
            candidates = self.graph.find_entity_candidates(
                entity.name, entity.entity_type, entity.aliases, entity.identifiers, limit=10
            )
            discovered[entity.temporary_id] = {
                "candidates": candidates,
                "neighborhoods": [
                    self.graph.get_entity_neighborhood(candidate["id"], max_hops=2, limit=15)
                    for candidate in candidates[:5]
                ],
            }
        return discovered
