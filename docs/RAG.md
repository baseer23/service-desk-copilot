# Retrieval-Augmented Generation (RAG) flow

```
+-----------------+        +-------------------------+
|  /ingest/paste  |        |       /ingest/pdf       |
|  /ingest/pdf    |        |  (extract text via PDF) |
+--------+--------+        +-----------+-------------+
         |                             |
         v                             v
    Chunk splitter  --->  Embeddings provider (Ollama | sentence-transformers | stub)
         |                             |
         v                             v
+----------------+            +-----------------------+
|  Chroma (HNSW) |            |  Neo4j Graph (APOC)   |
|  vector_store  |            |  Document/Chunk/Entity|
+--------+-------+            +-----------+-----------+
         |                                |
         +---------- stored ids ----------+

                Retrieval / Ask Pipeline
                -------------------------
                        |
                        v
                 Planner (GRAPH | VECTOR | HYBRID)
                        |
        +---------------+---------------+
        |                               |
        v                               v
  Vector search (Chroma)        Graph walk (Neo4j)
        \                               /
         \---> Hybrid filter ----------/
                        |
                        v
                 Answer synthesis
          (local SLM or stub, with citations)
```

- **Vector store**: Chroma keeps chunk embeddings on disk (`./store/chroma`).
- **Graph store**: Neo4j stores documents, chunks, and entities with relationships `HAS_CHUNK`, `ABOUT`, etc.
- **Planner**: examines detected entities and graph degree to choose the retrieval mode.
- **Retriever**: runs vector, graph, or hybrid constrained search.
- **Responder**: prompts the selected language model (stub/Ollama/llama.cpp), producing an answer with citations.
