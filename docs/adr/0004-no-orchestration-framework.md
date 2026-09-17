# ADR-004: no orchestration framework

## Status

Settled on day one. Unrevisited — nothing in five days of measurement gave a reason to add one.

## Context

`rag` calls the OpenAI SDK directly for both embedding and generation. There is no LangChain,
LlamaIndex, or similar retrieval-orchestration framework anywhere in this codebase. That is a
deliberate omission, not an oversight, and it is worth stating why given how default a LangChain
import has become in RAG tutorials and starter templates.

This project's actual thesis — the thing meant to distinguish it from "a working chatbot" — is
the set of decisions made by measurement: whether a lexical retrieval channel earns its place
(hit@5, fused vs. dense-only), whether stub pages help or hurt, where the retrieval floor sits,
which embedding model wins, how citation resolution and the refusal contract are enforced. Every
one of those decisions lives in code a reviewer can open and read in a few lines:
`backend/rag/rag/retrieval.py`'s Reciprocal Rank Fusion, `generate.py`'s citation-resolution and
refusal logic, `ingest.py`'s idempotency check. A framework's retriever/chain abstractions exist
precisely to hide exactly this kind of plumbing behind a configurable interface — which is a
reasonable thing to want in a system whose plumbing is not the point, and the wrong thing to want
in a system whose plumbing **is** the point being demonstrated.

There is also a concrete, non-rhetorical cost: LangChain's retriever/chain interfaces are built
around a general notion of "documents" and "chains" that does not natively express this system's
specific invariants — citation indices that must resolve to sources actually supplied, a
governed-facts block joined in alongside prose chunks, a small-talk intent branch with its own
leak check, a retrieval floor that gates before an API call is made. Implementing all of that
*through* a framework's abstractions would mean fighting the abstraction as often as using it, for
a corpus small enough (736 chunks) that the abstraction buys nothing a framework is actually good
at — orchestrating many heterogeneous tools, multi-step agentic loops, or swapping vector stores
without touching business logic. None of those apply here.

## Decision

**Call the `openai` SDK directly.** `backend/rag/rag/embedder.py` wraps embedding calls behind a
one-function `Embedder` Protocol (so the day-three embedder bake-off — see
[ADR-0005](0005-retrieval-and-embedding.md) — is a config change, not a refactor, and so tests can
substitute a fake without a real API call); `generate.py` wraps the completion call behind an
equally small `Completion` Protocol for the same reason. Structured output uses the OpenAI
Responses API's strict JSON schema mode directly, with a single repair-retry on invalid JSON before
failing closed to a refusal.

## Consequences

- Every retrieval and generation decision is legible in the service's own code, in the file the
  README and the ADRs point a reviewer at — there is no framework internals to also understand
  before the actual decision is visible.
- Two small Protocol seams (`Embedder`, `Completion`) give the same substitutability a framework's
  abstraction layer would, for exactly the two calls that need it, without adopting a
  general-purpose abstraction for a system that only ever calls one provider.
- Adding an actual orchestration need later — multiple tool-calling steps, multiple providers
  behind one interface, an agentic loop — is the point at which this decision should be revisited.
  Nothing here claims a framework is never justified; this system's shape, today, does not need
  one.
- One fewer dependency to pin, update, and explain the version-compatibility surface of, in a
  five-day build where every added dependency needs a one-line justification (a standing rule for
  this project's dependency choices).

## Rejected

- **LangChain.** The default choice for a RAG tutorial, and the one most likely to obscure the
  actual decisions this project exists to demonstrate — retrieval fusion, the grounding contract,
  citation resolution — behind chain and retriever abstractions built for a more general problem
  than this one.
- **LlamaIndex.** Similar reasoning: strong for rapid prototyping over heterogeneous data sources
  and index types, which is not this project's problem. This project has one data source, one
  index shape, and a small number of specific, measured decisions about how retrieval and
  generation behave — exactly the layer LlamaIndex's abstractions would sit on top of and partly
  hide.
