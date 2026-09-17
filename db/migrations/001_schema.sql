-- db/migrations/001_schema.sql   apply with: psql -v dim="$EMBEDDING_DIM" -f this
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS rag;
CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE rag.corpora (
  id uuid PRIMARY KEY, content_hash text NOT NULL, manifest jsonb NOT NULL,
  doc_count int, chunk_count int, dropped_count int,
  embedding_model text NOT NULL, embedding_version int,
  status text NOT NULL, stage text, error text,
  created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(),
  -- The idempotency key is (content_hash, embedding_model), not
  -- content_hash alone: re-ingesting the same corpus under a second
  -- embedding model is a legitimate, required state (never
  -- silently mix vector spaces from two models in one index), so the same
  -- content_hash must be allowed to appear once per model. A column-level
  -- UNIQUE on content_hash cannot express that; embedding_model must also be
  -- NOT NULL so it can sit in a composite key (a NULL never equals another
  -- NULL, so a nullable column can't carry a uniqueness guarantee).
  UNIQUE (content_hash, embedding_model)
);

CREATE TABLE rag.documents (
  id uuid PRIMARY KEY, corpus_id uuid NOT NULL REFERENCES rag.corpora(id) ON DELETE CASCADE,
  url text NOT NULL, canonical_url text, title text,
  section_path text[], source_class text NOT NULL,
  status text, valid_from date, valid_to date,
  text text NOT NULL, content_hash text NOT NULL, lang text, fetched_at timestamptz
);

CREATE TABLE rag.chunks (
  id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES rag.documents(id) ON DELETE CASCADE,
  corpus_id uuid NOT NULL, ord int NOT NULL,
  text text NOT NULL, embed_input text NOT NULL, token_count int,
  source_class text NOT NULL,
  embedding vector(:dim) NOT NULL,
  embedding_model text NOT NULL, embedding_version int NOT NULL DEFAULT 1,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE rag.product_facts (
  id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES rag.documents(id) ON DELETE CASCADE,
  corpus_id uuid NOT NULL, product_slug text, attribute text NOT NULL,
  value_num numeric, value_text text, unit text, currency text,
  raw_fragment text, source_url text, extractor_version int, observed_at timestamptz
);

CREATE TABLE app.interactions (
  id uuid PRIMARY KEY, session_id text, corpus_id uuid,
  question text NOT NULL, answer text,
  grounded bool NOT NULL, refused bool NOT NULL, refusal_class text, error text,
  citations jsonb, retrieval jsonb, facts_used jsonb,
  model text, prompt_version text,
  prompt_tokens int, completion_tokens int, cost_usd numeric(10,6),
  retrieval_ms int, generation_ms int, latency_ms int,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX chunks_embedding_hnsw ON rag.chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX chunks_corpus_model ON rag.chunks (corpus_id, embedding_model);
CREATE INDEX facts_document ON rag.product_facts (document_id);
CREATE INDEX interactions_created ON app.interactions (created_at);
CREATE INDEX interactions_session ON app.interactions (session_id, created_at);
