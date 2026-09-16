# ADR-003: two services, not a modulith or three

## Status

Settled on day one, unrevisited during the build. The internal spec's own overengineering audit
(`CUT_LIST.md` item A1) originally recommended the opposite seam — this ADR records why that
recommendation was overridden.

## Context

The brief's microservice requirement sits under "Conversational Interface" and names exactly two
activities: "microservice architecture for question handling and response generation using JSON
format." That sentence names the two things it asks to be separated. The seam this build draws is
the one the sentence names, not a seam invented to look more distributed than the brief asked for.

- **`chat` owns question handling.** Request validation, session id, PII redaction at log-write,
  writing every interaction to the database, and serving analytics over that record.
- **`rag` owns response generation.** Retrieval, prompt assembly, the OpenAI call, citation
  resolution. It also owns ingestion, because chunking and embedding exist only to serve
  retrieval — splitting them out would put one data lifecycle (a document's path from scrape to
  embedded, retrievable chunk) across two service owners for no reader of that data to benefit
  from.
- **One database, two schemas, no cross-schema reads.** `chat` never reads or writes `rag.*`;
  `rag` never reads or writes `app.*`. `rag` is the only service that ever calls OpenAI, so there
  is exactly one service to audit for key handling in application code (see the key-isolation
  caveat in the README's Known Limitations, which is about the container environment, not this
  seam).

**State the tradeoff plainly, because naming it is worth more than defending it.** At the corpus
size this build actually ships — 736 chunks — a single service would be simpler to build, deploy
and reason about, and would very likely also be faster end-to-end, with no cross-service HTTP hop
between `chat` and `rag` on every question. The split exists for two reasons that are not "it
scales better": the brief asks for it by name, and the two halves change for genuinely different
reasons even at this size — request-and-record concerns (rate limiting, redaction, analytics
aggregation) change for product reasons, while retrieval-and-generation concerns (embedding model,
fusion weights, prompt rules) change for model reasons. A design that defends microservices on
performance or scale grounds at 736 chunks would be wrong; a design that names the phase-dependence
out loud and ships the seam the brief asked for anyway is the honest answer.

## Decision

**Two services: `chat` and `rag`, JSON over HTTP, one internal endpoint (`POST /answer`) between
them.** `chat` is the only one with a public surface beyond the two services' shared `/healthz`;
`rag`'s public surface is limited to corpus upload and status (`POST /api/v1/corpora`,
`GET /api/v1/corpora/{id}`), proxied through nginx — its `/answer` endpoint publishes no port and
is reachable only from `chat`'s container on the compose network.

## Consequences

- A reviewer auditing "does this satisfy R8" finds exactly the two services the brief's sentence
  names, doing exactly the two things it names, with no third service diluting the answer.
- The `chat → rag` hop is real network latency on every question — visible in the eval report's
  own latency table, and part of why the end-to-end latency budget is missed at p95 (see the
  README's measured-numbers section). This is the direct, disclosed cost of the split, not a
  hidden one.
- One database, two schemas, and the no-cross-schema-read rule keep the operational simplicity of
  a single Postgres instance ([ADR-0002](0002-pgvector-over-a-dedicated-vector-database.md))
  without collapsing the two services' ownership boundary back into a shared table both write.
- Growth past the point named in this ADR's own tradeoff sentence — meaningfully more than a few
  thousand chunks, or genuinely independent scaling needs for ingest versus question traffic —
  is the point to revisit whether `rag`'s ingestion half should split out on its own, not before.

## Rejected

- **A modulith** (one service, both concerns as internal modules). Simpler to build and probably
  faster to answer a question, but it does not satisfy R8's literal ask for a microservice split
  between question handling and response generation — a candidate choosing this at this scale
  would be optimising for an outcome the brief did not request.
- **Three services, with ingestion split out from `rag`** (`ingest` + `chat` + `rag`, or similar).
  This divides one data lifecycle — a document's path from scrape to embedded chunk — across two
  owners for no benefit: nothing reads ingested-but-not-yet-searchable data independently of the
  retrieval path it feeds, so the split adds a network hop and a second thing to keep in sync with
  no reader on the other side of the boundary.
- **The `ingestion` + `chat` seam** (this repo's own earlier plan, `CUT_LIST.md` item A1, and the
  seam the internal overengineering audit originally recommended). It divides along an axis the
  brief never names ("ingestion" versus "everything else") while leaving the axis the brief does
  name — question handling versus response generation — entirely undivided inside "everything
  else." A reviewer checking R8 against this seam would have to infer the mapping rather than read
  it off the service names.
