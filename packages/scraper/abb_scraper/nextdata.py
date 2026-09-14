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
_ANCHOR = re.compile(r'"state":\{"data":\{"data":\[\{')
_PARENT = re.compile(r'"parent":\{')
_RECORD_URL = re.compile(r'"url":"([^"]*)"')
_HEAD = 400  # a CMS page record carries "slug" within its first few keys


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


def _object_end(s: str, open_brace: int) -> int:
    """Index just past the `}` that closes the `{` at `open_brace`, tracking
    JSON string state so a brace inside a string cannot shift the depth."""
    depth = 0
    in_string = escaped = False
    for i in range(open_brace, len(s)):
        c = s[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(s)


def _strip_parents(record: str) -> str:
    """Remove every nested `"parent":{…}` subtree.

    A page record carries its whole ancestor chain under `parent`, and each
    ancestor carries ITS OWN sections -- including its FAQ accordion. Left in,
    `/ferdi/kreditler/emanetci-nagd-krediti` inherits the five questions that
    belong to `/ferdi/kreditler` and that its page does not render.
    """
    out = record
    while (m := _PARENT.search(out)) is not None:
        out = out[: m.start()] + out[_object_end(out, m.end() - 1) :]
    return out


def own_page_record(flight: str, url_path: str) -> str:
    """The current page's own CMS record, ancestors excised.

    The payload is not one page's data: it carries the React-Query dehydrated
    cache (`"state":{"data":{"data":[{…`), and the page record inside it nests
    its parent chain. Scanning the whole payload therefore attributes an
    ancestor's -- and sometimes a sibling's -- FAQ to this page. Matching on
    the record's own `url` is what keeps the attribution honest; a page whose
    record cannot be identified contributes nothing rather than guessing.

    One exception, and only one: when the payload describes exactly one page
    and that page is not at `url_path`, the URL was a redirect alias and the
    single record IS the page that was served. Measured: this happens on one
    document, `ferdi/kartlar/debet-kartlari/abb-miles`, which redirects to
    `.../azal-miles-visa-platinum-mc-black-edition`.
    """
    want = url_path.strip("/")
    page_records: list[str] = []
    for m in _ANCHOR.finditer(flight):
        start = m.end() - 1
        record = flight[start : _object_end(flight, start)]
        found = _RECORD_URL.search(record)
        if found is not None and found.group(1).strip("/") == want:
            return _strip_parents(record)
        if '"slug":"' in record[:_HEAD]:
            page_records.append(record)
    if len(page_records) == 1:
        return _strip_parents(page_records[0])
    return ""


def _text(html_fragment: str) -> str:
    """Flatten an answer's HTML to plain text.

    Block boundaries are turned into spaces *before* parsing rather than using
    selectolax's `text(separator=" ")`: much of this content is pasted from
    Word, which wraps individual Azerbaijani characters in their own <span>
    (`<span>ç</span>ox`), and a per-text-node separator shatters those words
    into "ç ox".
    """
    return re.sub(r"\s+", " ", HTMLParser(_BLOCK_END.sub(" ", html_fragment)).text()).strip()


def faq_pairs(html: str, url_path: str) -> list[tuple[str, str]]:
    """(question, answer) for every accordion item THIS page renders.

    Scoped to the page's own record (see `own_page_record`) -- the payload
    also carries the accordions of every ancestor route, which the page does
    not render.

    Dropped: items whose answer is an unresolvable reference, is empty, or
    merely repeats the question (ABB ships a handful of unfilled "Bura metn
    yazilmalidir" placeholders). Duplicates within one page are collapsed --
    the same FAQ tab is occasionally mounted twice on one page.
    """
    flight = flight_payload(html)
    if not flight:
        return []
    record = own_page_record(flight, url_path)
    if not record:
        return []
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    # References are resolved against the WHOLE payload: a `$36` answer inside
    # the page record points at a flight row emitted outside it.
    for m in _QA.finditer(record):
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
