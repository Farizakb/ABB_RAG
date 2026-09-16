# services/rag/app/ingest.py
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import psycopg
from contracts.models import Corpus, Document

from app.chunking import Chunk, chunk_document, tokens_per_char
from app.db import get_conn
from app.embedder import Embedder

# P72: how long a `processing` row may go without a stage heartbeat before a
# new ingest attempt treats it as abandoned rather than in flight. Named here
# so the call site reads as policy, not an unexplained literal.
STALE_PROCESSING_AFTER = timedelta(minutes=5)


def _schema_dim(conn: psycopg.Connection) -> int:
    row = conn.execute(
        "SELECT atttypmod FROM pg_attribute "
        "WHERE attrelid = 'rag.chunks'::regclass AND attname = 'embedding'"
    ).fetchone()
    if row is None:
        raise RuntimeError(
            "rag.chunks.embedding column not found — has the migration been applied?"
        )
    return int(row[0])


def _stage(conn: psycopg.Connection, row_id: uuid.UUID, stage: str) -> None:
    """P67: commits immediately so a concurrent observer sees the stage
    transition (and its updated_at bump) while the ingest is still running,
    not only once the whole thing finishes -- Task 17's stall detector and
    P72's staleness check both depend on this being a live signal."""
    conn.execute("UPDATE rag.corpora SET stage=%s, updated_at=now() WHERE id=%s", (stage, row_id))
    conn.commit()


def _product_slug(doc: Document) -> str:
    """P73: product_facts.product_slug must be a slug, not doc.title -- Task
    18's golden set and the SPEC §7.3 fact-governance path both read this
    column as one. Derived from the final non-empty path segment of doc.url
    (a trailing slash, query string, and fragment are ignored), falling back
    to doc.title when the URL has no usable segment (e.g. a bare origin)."""
    url: str = doc.url
    title: str = doc.title
    segments = [s for s in urlsplit(url).path.split("/") if s]
    return segments[-1] if segments else title


def ingest_corpus(corpus: Corpus, embedder: Embedder) -> str:
    """Idempotent on (corpus_id, embedding_model). Stages are written to the
    corpus row as they progress so the UI can poll (SPEC §6.3)."""
    # explicit annotation: packages/contracts ships no py.typed marker, so
    # mypy resolves attribute access on an installed (non-stub) Corpus as
    # Any; without this the two `return corpus_id` below trip no-any-return.
    corpus_id: str = corpus.corpus_id

    with get_conn() as conn:
        schema_dim = _schema_dim(conn)
        if embedder.dim != schema_dim:
            raise ValueError(
                f"embedding dimension {embedder.dim} does not match the schema "
                f"{schema_dim} — set EMBEDDING_DIM and re-apply the migration"
            )

        existing = conn.execute(
            "SELECT id, status, now() - updated_at > %s AS stale "
            "FROM rag.corpora WHERE content_hash = %s AND embedding_model = %s",
            (STALE_PROCESSING_AFTER, corpus_id, embedder.model),
        ).fetchone()
        if existing:
            existing_id, status, stale = existing
            # P72: a `processing` row with no heartbeat in STALE_PROCESSING_AFTER
            # means the ingest that owned it crashed -- the retry SPEC §6.3
            # promises must reach these rows, not just ones already marked
            # `failed` (P66), or a crash leaves the corpus stuck forever.
            if status == "failed" or (status == "processing" and stale):
                # Cascades through documents to chunks and product_facts.
                conn.execute("DELETE FROM rag.corpora WHERE id = %s", (existing_id,))
                conn.commit()
            else:
                # status == 'ready', or a FRESH 'processing' row (another
                # ingest is already in flight) — either way, don't re-ingest.
                return corpus_id

        row_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO rag.corpora (id, content_hash, manifest, status, stage, "
            "embedding_model, embedding_version) VALUES (%s,%s,%s,'processing','validating',%s,1)",
            (row_id, corpus_id, json.dumps(corpus.stats), embedder.model),
        )
        conn.commit()

        pending: list[tuple[uuid.UUID, Chunk, str]] = []
        fact_count = 0
        for doc in corpus.documents:
            doc_id = uuid.uuid4()
            conn.execute(
                "INSERT INTO rag.documents (id, corpus_id, url, canonical_url, title, "
                "section_path, source_class, status, valid_from, valid_to, text, "
                "content_hash, lang, fetched_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    doc_id,
                    row_id,
                    doc.url,
                    doc.canonical_url,
                    doc.title,
                    doc.section_path,
                    doc.source_class,
                    doc.status,
                    doc.valid_from,
                    doc.valid_to,
                    doc.text,
                    doc.content_hash,
                    doc.lang,
                    doc.fetched_at,
                ),
            )
            for fact in doc.facts:
                conn.execute(
                    "INSERT INTO rag.product_facts (id, document_id, corpus_id, product_slug, "
                    "attribute, value_num, value_text, unit, currency, raw_fragment, "
                    "source_url, extractor_version, observed_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,now())",
                    (
                        uuid.uuid4(),
                        doc_id,
                        row_id,
                        _product_slug(doc),
                        fact.attribute,
                        fact.value_num,
                        fact.value_text,
                        fact.unit,
                        fact.currency,
                        fact.raw_fragment,
                        doc.url,
                    ),
                )
                fact_count += 1
            for chunk in chunk_document(doc):
                pending.append((doc_id, chunk, doc.source_class))
        conn.commit()

        _stage(conn, row_id, "embedding")

        vectors = embedder.embed([c.embed_input for _, c, _ in pending])

        _stage(conn, row_id, "indexing")
        for (doc_id, chunk, source_class), vector in zip(pending, vectors, strict=True):
            conn.execute(
                "INSERT INTO rag.chunks (id, document_id, corpus_id, ord, text, embed_input, "
                "token_count, source_class, embedding, embedding_model, embedding_version) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)",
                (
                    uuid.uuid4(),
                    doc_id,
                    row_id,
                    chunk.ord,
                    chunk.text,
                    chunk.embed_input,
                    chunk.token_count,
                    source_class,
                    str(vector),
                    embedder.model,
                ),
            )

        conn.execute(
            "UPDATE rag.corpora SET status='ready', stage='ready', doc_count=%s, "
            "chunk_count=%s, manifest = manifest || %s, updated_at=now() WHERE id=%s",
            (
                len(corpus.documents),
                len(pending),
                json.dumps(
                    {
                        "fact_count": fact_count,
                        "tokens_per_char": tokens_per_char([d.text for d in corpus.documents]),
                    }
                ),
                row_id,
            ),
        )
        conn.commit()

    return corpus_id
