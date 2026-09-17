# ADR-002: pgvector over a dedicated vector database

## Status

Settled. Unrevisited since the day-one stack decision; nothing measured during the build changed
it.

## Context

The requirement is extracted data "formatted into a suitable vector database format," framework
of choice. At the corpus size this build actually ships — 280 documents, 736 chunks — the choice
of vector store has no measurable effect on retrieval quality or latency; what it affects is
operational surface area: how many systems exist, how many connection strings, credentials and
backup jobs a reviewer (or a future ABB operator) has to reason about.

`rag.chunks.embedding` is a `vector` column (dimension from `EMBEDDING_DIM`) alongside ordinary
relational columns — `document_id`, `corpus_id`, `ord`, `text`, `embedding_model`,
`embedding_version` — on the same row. Every retrieval query already needs to join vector
similarity against relational filters (`corpus_id`, `embedding_model`) and, separately, against
`rag.product_facts`. Keeping the vectors in the same engine as everything they are joined against
is not a convenience on top of the real design — for this system, it **is** the design: hybrid
retrieval (dense + full-text + trigram, fused by Reciprocal Rank Fusion — see
[ADR-0005](0005-retrieval-and-embedding.md)) runs all three legs as SQL against one database,
which a split vector-store-plus-relational-store architecture could not do without either
duplicating the lexical index into the vector store or fanning every query out across two
systems and fusing client-side.

## Decision

**Postgres 16 with the `pgvector` extension. One engine, one connection story, one backup story.**
`rag.chunks.embedding` uses HNSW indexing, `vector_cosine_ops`, and pgvector's own defaults
(`m=16`, `ef_construction=64`) — not tuned, and the code says so rather than implying they were:
at 736 chunks, index-parameter tuning is not where any measured problem lives, and tuning
untested parameters would be decoration, not engineering.

**The migration threshold, stated rather than implied away.** pgvector does not scale forever,
and this ADR does not pretend it does. It is a strong choice for this corpus's scale (low
thousands of chunks, one write path, one reader) precisely because there is nothing here that
needs a dedicated vector engine's specialised strengths — sharded ANN indexes across many nodes,
purpose-built quantization for tens of millions of vectors, or a separate scaling axis for vector
query load versus relational query load. Once any of those becomes true — the corpus grows by
orders of magnitude, embedding-query QPS needs to scale independently of the relational workload,
or a single Postgres primary can no longer serve both comfortably — that is the point to revisit
this decision, not before. This is a reasoned threshold, not a measured one: nothing in this
build's eval or load testing exercised anything close to that scale, and this ADR does not claim
otherwise.

## Consequences

- One database to back up, one connection pool, one set of credentials, one migration path
  (`db/migrations/*.sql`, numbered, no framework — see the rejected Alembic note in the internal
  spec). A reviewer auditing "where does data live" has one answer, not two.
- `chat` and `rag` share the physical database but not schemas — `app.*` and `rag.*`, with no
  cross-schema reads (an enforced invariant, not just a convention) — so the operational
  simplicity of one engine does not collapse the ownership boundary the two-service split exists
  to draw. See [ADR-0003](0003-two-services.md).
- Retrieval-quality decisions (dense vs. hybrid, the embedding model, the retrieval floor) are
  entirely independent of this ADR — they are measured and settled in
  [ADR-0005](0005-retrieval-and-embedding.md) and would be unchanged by a different vector store.
- HNSW's default parameters are genuinely not doing meaningful work at this scale — the README
  and this ADR say so rather than presenting an untuned index as a tuned one.

## Rejected

- **Qdrant.** A dedicated, well-regarded vector database — but it introduces a second system to
  run, back up and credential, for a corpus where the relational joins (against `product_facts`,
  against the lexical legs) are as important to every query as the vector similarity is. Splitting
  those across two engines would mean fusing results client-side instead of in one SQL query.
- **Chroma.** Similar reasoning, with less operational maturity for a production-facing service
  than either Postgres or a dedicated ANN engine designed for exactly this workload.
- **FAISS.** A library, not a service — it would need to be embedded inside `rag` with no
  persistence, replication or backup story of its own, all of which Postgres already provides for
  the relational data this system needs regardless of vector-store choice. Rebuilding that
  operational layer around a library, for a corpus this size, buys nothing measurable.
