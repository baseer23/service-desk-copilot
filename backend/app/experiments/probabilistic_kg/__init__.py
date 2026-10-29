"""Probabilistic knowledge-graph construction and retrieval experiment (dev_exp).

Approach A is the existing dev pipeline (structural projection plus
``DocumentGraphUnderstanding`` / ``GraphPlacementService``). Approaches B and C
share one probabilistic reasoning and validation pipeline and differ only in
the write transport: the Python Neo4j driver (B) or the official neo4j-mcp
``write-cypher`` tool on a separate, restricted connection (C).
"""
