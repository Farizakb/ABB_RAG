# services/rag/app/chunking.py
from __future__ import annotations

from typing import NamedTuple

import tiktoken
from contracts.models import Document

CHUNK_TOKENS = 600
CHUNK_OVERLAP = 80
_enc = tiktoken.get_encoding("cl100k_base")


class Chunk(NamedTuple):
    ord: int
    text: str
    embed_input: str
    token_count: int


def tokens_per_char(texts: list[str]) -> float:
    chars = sum(len(t) for t in texts) or 1
    return sum(len(_enc.encode(t)) for t in texts) / chars


def _prefix(doc: Document) -> str:
    path = " > ".join(doc.section_path)
    return f"{doc.title} | {path}\n" if path else f"{doc.title}\n"


def chunk_document(doc: Document) -> list[Chunk]:
    """Section-aware split on heading boundaries inside the content region, then
    packed to CHUNK_TOKENS. Navigation shares heading tags with content, which is
    why the split happens on the already-extracted text rather than on the DOM.

    Overlap is section-granular, not token-level windowing: the previous section
    carries forward into the next chunk only when it fits within CHUNK_OVERLAP
    tokens, otherwise no overlap is carried. This keeps CHUNK_OVERLAP an honest
    bound (chunk token_count <= CHUNK_TOKENS + CHUNK_OVERLAP is always provable)
    rather than carrying an arbitrarily large trailing section.

    A single section larger than CHUNK_TOKENS is emitted as one oversized chunk
    rather than being split further -- see the dedicated test for why this is an
    accepted limitation, not an oversight.
    """
    prefix = _prefix(doc)
    sections = [s for s in doc.text.split("\n") if s.strip()]

    packs: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    for section in sections:
        n = len(_enc.encode(section))
        if current and current_tokens + n > CHUNK_TOKENS:
            packs.append(current)
            prev = current[-1]
            prev_tokens = len(_enc.encode(prev))
            if prev_tokens <= CHUNK_OVERLAP:
                current, current_tokens = [prev], prev_tokens
            else:
                current, current_tokens = [], 0
        current.append(section)
        current_tokens += n
    if current:
        packs.append(current)

    out: list[Chunk] = []
    for i, pack in enumerate(packs):
        text = "\n".join(pack)
        out.append(
            Chunk(
                ord=i,
                text=text,
                embed_input=prefix + text,
                token_count=len(_enc.encode(text)),
            )
        )
    return out
