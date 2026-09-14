# packages/scraper/abb_scraper/nextdata.py
"""FAQ/accordion recovery from Next.js RSC flight payloads.

ABB renders every "Tez-tez verilən suallar" / "Sual-cavab" section as a
client-side accordion: the questions and answers are NOT in the served DOM,
they live only inside `self.__next_f.push([1,"..."])` script chunks, which
`extract.content_blocks` decomposes along with every other <script>. Measured
on the 2026-09-13 crawl: 113 of 243 kept documents carry accordion content in
the flight payload, and 96 of those lost all of it -- 772 question/answer
pairs, ~190k characters of exactly the Q&A shape a RAG answers best from.

The payload is a stream of JS string literals that must be concatenated before
parsing: a single flight row is routinely split across two pushes, so scanning
the pushes individually cuts objects in half.
"""

from __future__ import annotations

import json
import re

from selectolax.parser import HTMLParser

_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
_QA = re.compile(r'"trigger":"((?:[^"\\]|\\.)*)","content":"((?:[^"\\]|\\.)*)"')
_REF = re.compile(r"^\$(\w+)$")


def _unescape(literal: str) -> str:
    return str(json.loads(f'"{literal}"'))


def flight_payload(html: str) -> str:
    """The concatenated RSC flight stream, or "" on a non-Next.js page."""
    return "".join(_unescape(m.group(1)) for m in _PUSH.finditer(html))


def _deref(flight: str, value: str) -> str:
    """Resolve a `$36` flight reference to its row body.

    Referenced rows are emitted as `36:T<hex-byte-length>,<body>` -- the length
    is in UTF-8 bytes, not characters, which matters on Azerbaijani text.

    The row is NOT reliably at the start of a line (a preceding row's body can
    end without a newline, and one observed page emits `...</p>3a:Ta6c,<p>...`),
    so the anchor is "not preceded by an alphanumeric" instead. The `:T<hex>,`
    shape is what keeps that loose anchor honest -- it is why a `37:` inside a
    timestamp such as `07:37:59.169Z` cannot match.
    """
    ref = _REF.match(value)
    if ref is None:
        return value
    row = re.search(rf"(?<![0-9A-Za-z]){re.escape(ref.group(1))}:T([0-9a-f]+),", flight)
    if row is None:
        return ""
    length = int(row.group(1), 16)
    return flight[row.end() :].encode()[:length].decode(errors="ignore")


_BLOCK_END = re.compile(r"(?i)</(?:p|li|ul|ol|div|tr|td|th|h[1-6])\s*>|<br\s*/?>")


def _text(html_fragment: str) -> str:
    """Flatten an answer's HTML to plain text.

    Block boundaries are turned into spaces *before* parsing rather than using
    selectolax's `text(separator=" ")`: much of this content is pasted from
    Word, which wraps individual Azerbaijani characters in their own <span>
    (`<span>ç</span>ox`), and a per-text-node separator shatters those words
    into "ç ox".
    """
    return re.sub(r"\s+", " ", HTMLParser(_BLOCK_END.sub(" ", html_fragment)).text()).strip()


def faq_pairs(html: str) -> list[tuple[str, str]]:
    """(question, answer) for every accordion item in the flight payload.

    Dropped: items whose answer is an unresolvable reference, is empty, or
    merely repeats the question (ABB ships a handful of unfilled "Bura metn
    yazilmalidir" placeholders). Duplicates within one page are collapsed --
    the same FAQ tab is occasionally mounted twice on one page.
    """
    flight = flight_payload(html)
    if not flight:
        return []
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in _QA.finditer(flight):
        question = _text(_unescape(m.group(1)))
        answer = _text(_deref(flight, _unescape(m.group(2))))
        if not question or not answer or answer == question:
            continue
        pair = (question, answer)
        if pair in seen:
            continue
        seen.add(pair)
        out.append(pair)
    return out
