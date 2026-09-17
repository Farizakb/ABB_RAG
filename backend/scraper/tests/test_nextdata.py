# backend/scraper/tests/test_nextdata.py
# ruff: noqa: RUF001, RUF002 -- this file quotes a lot of genuine Azerbaijani
# fixture/assertion text (dotless-i and friends); it isn't ambiguous, it's AZ.
import json
from pathlib import Path

from abb_scraper.extract import FAQ_TAG, Block, content_blocks, extract_page, faq_blocks
from abb_scraper.nextdata import faq_pairs, flight_payload

FIXTURES = Path("backend/scraper/tests/fixtures/raw")
NAGD_KREDIT = "/ferdi/kreditler/nagd-kredit"
KREDITLER = "/ferdi/kreditler"
PATH = "ferdi/test"


def push(*rows: str) -> str:
    """Wrap flight rows the way Next.js serialises them: one or more
    `self.__next_f.push([1,"<js string literal>"])` script tags.
    """
    return "".join(f"<script>self.__next_f.push([1,{json.dumps(r)}])</script>" for r in rows)


def item(question: str, answer_html: str) -> str:
    """One accordion item as it appears inside the flight stream: a JSON
    object whose markup is `\\u003c`-escaped, exactly as ABB emits it.
    """
    obj = json.dumps({"trigger": question, "content": answer_html}, separators=(",", ":"))
    return obj.replace("<", "\\u003c")


def record(items: str, url: str = PATH, parent: str = "") -> str:
    """The dehydrated page record the payload actually carries: React Query's
    `state.data.data[0]`, with the page's sections inside it and, optionally,
    an ancestor chain under `parent`.
    """
    tail = f',"parent":{parent}' if parent else ""
    slug = url.rsplit("/", 1)[-1]
    return (
        f'"state":{{"data":{{"data":[{{"id":1,"slug":"{slug}","url":"{url}"'
        f',"sections":[{items}]{tail}}}]}}}}'
    )


def test_nagd_kredit_faq_is_recovered_and_is_not_in_the_dom() -> None:
    """The page the whole extraction pipeline was derived from carries a
    "Nağd kredit haqqında sual-cavab" accordion that never reaches the DOM.
    """
    html = (FIXTURES / "nagd-kredit.html").read_text(encoding="utf-8")
    pairs = faq_pairs(html, NAGD_KREDIT)
    questions = {q for q, _ in pairs}

    assert len(pairs) >= 15
    assert "Nağd kredit nədir?" in questions
    # Flight-only: the served markup has the accordion's data but not its text.
    body = html.split("__next_f")[0]
    assert "Nağd kredit nədir?" not in body

    answer = next(a for q, a in pairs if q == "Nağd kredit nədir?")
    assert "kredit" in answer.lower()
    assert "<" not in answer


def test_an_ancestors_faq_is_not_attributed_to_its_child_page() -> None:
    """The payload nests the whole parent chain, and each ancestor carries its
    own sections. `nagd-kredit`'s payload therefore contains the five questions
    that belong to the `/ferdi/kreditler` listing page -- which `nagd-kredit`
    does not render. Proven both ways on real fixtures: the question is present
    in the child's raw payload and absent from the child's pairs, and it is
    present in the pairs of the page that does own it.
    """
    child = (FIXTURES / "nagd-kredit.html").read_text(encoding="utf-8")
    parent = (FIXTURES / "listing-ferdi-kreditler.html").read_text(encoding="utf-8")
    parent_question = "Kredit üçün onlayn müraciət etmək mümkündür?"

    assert parent_question in flight_payload(child), "fixture no longer carries the parent's FAQ"
    assert parent_question not in {q for q, _ in faq_pairs(child, NAGD_KREDIT)}
    assert parent_question in {q for q, _ in faq_pairs(parent, KREDITLER)}


def test_a_page_whose_record_cannot_be_identified_contributes_nothing() -> None:
    """Attribution is the whole point: a payload carrying several pages, none
    of them this URL, must yield nothing rather than everything it holds."""
    html = push(
        record(item("Sual?", "<p>Cavab.</p>"), url="ferdi/a")
        + record(item("Başqa?", "<p>Cavab.</p>"), url="ferdi/b")
    )
    assert faq_pairs(html, "/some/other/page") == []


def test_a_redirect_alias_falls_back_to_the_single_page_record_served() -> None:
    """`ferdi/kartlar/debet-kartlari/abb-miles` redirects to the canonical
    card page, so the fetched URL never appears in the payload. One record
    means one page, and it is the page that was served."""
    html = push(record(item("Sual?", "<p>Cavab.</p>"), url="ferdi/canonical"))
    assert faq_pairs(html, "/ferdi/alias") == [("Sual?", "Cavab.")]


def test_item_split_across_two_pushes_is_recovered() -> None:
    """A single flight row is routinely cut in half between two pushes; the
    payload must be concatenated before it is scanned.
    """
    row = record(item("Kredit nədir?", "<p>Bank vəsaitidir.</p>"))
    cut = row.index("dir?")
    expected = [("Kredit nədir?", "Bank vəsaitidir.")]
    assert faq_pairs(push(row[:cut], row[cut:]), PATH) == expected


def test_referenced_answer_is_sliced_by_utf8_bytes_not_characters() -> None:
    """`$36` answers point at a `36:T<hex>,` row whose length is in UTF-8
    bytes. Azerbaijani text has more bytes than characters, so a character
    slice over-reads into whatever the stream emits next. The row lives
    OUTSIDE the page record, so references resolve against the whole payload.
    """
    body = "<p>Şərtlər əlverişlidir.</p>"
    row = f"36:T{len(body.encode()):x},{body}\n37:TRAILING-JUNK"
    html = push(record(item("Şərtlər", "$36")) + "\n" + row)

    assert faq_pairs(html, PATH) == [("Şərtlər", "Şərtlər əlverişlidir.")]


def test_reference_row_not_at_line_start_is_still_resolved() -> None:
    """Observed on biznes/**/kocurmeler: the referenced row follows the
    previous row's body on the same line (`...</p>3a:Ta6c,<p>...`).
    """
    body = "<p>Sened teleb olunur.</p>"
    row = f"3a:T{len(body.encode()):x},{body}"
    html = push(record(item("Məlumat", "$3a")) + "</p>" + row)
    assert faq_pairs(html, PATH) == [("Məlumat", "Sened teleb olunur.")]


def test_placeholder_unresolvable_and_duplicate_items_are_dropped() -> None:
    html = push(
        record(
            ",".join(
                [
                    item("Bura metn yazilmalidir", "<p>Bura metn yazilmalidir</p>"),
                    item("Yoxdur", "$99"),
                    item("Qiymət?", "<p>5 AZN</p>"),
                    item("Qiymət?", "<p>5 AZN</p>"),
                ]
            )
        )
    )
    assert faq_pairs(html, PATH) == [("Qiymət?", "5 AZN")]


def test_word_split_across_inline_spans_is_not_shattered() -> None:
    """Much of this content is pasted from Word, which wraps single
    Azerbaijani characters in their own <span>. A per-text-node separator
    turns "çox" into "ç ox".
    """
    html = push(record(item("Sual?", "<p><span>ç</span>ox hallarda</p><p>ikinci</p>")))
    assert faq_pairs(html, PATH) == [("Sual?", "çox hallarda ikinci")]


def test_a_brace_inside_answer_text_does_not_end_the_record() -> None:
    """The record span is found by brace matching, so a `{` in the content --
    ABB ships validation templates like "Yanlış {validation}" -- must not be
    counted when it sits inside a JSON string.
    """
    parent = f'{{"id":2,"url":"ferdi","sections":[{item("Ata sualı?", "<p>Ata.</p>")}]}}'
    html = push(record(item("Sual?", "<p>Yanlış {validation} }}</p>"), parent=parent))

    assert faq_pairs(html, PATH) == [("Sual?", "Yanlış {validation} }}")]


def test_extract_page_carries_the_faq_into_the_document_text() -> None:
    html = (FIXTURES / "nagd-kredit.html").read_text(encoding="utf-8")
    page = extract_page(html, f"https://abb-bank.az{NAGD_KREDIT}")

    q, a = next((q, a) for q, a in faq_pairs(html, NAGD_KREDIT) if q == "Nağd kredit nədir?")
    assert f"{q} {a}" in page.text
    # Question and answer share one line: `chunk_document` splits on line
    # boundaries and must not be able to strand a question from its answer.
    assert any(q in line and a in line for line in page.text.split("\n"))


def test_faq_answer_missing_from_dom_is_still_recovered_even_when_question_is_rendered() -> None:
    """15 live pages server-render the accordion's question triggers but never
    their answers -- the answer exists only in the flight payload. The old
    question-keyed skip mistook "question already in DOM" for "pair already
    in DOM" and discarded the pair, answer included: 76 pairs / 25,903 chars
    lost across 19 pages, every one of them losing every answer it had
    (measured 2026-09-14 on the 550-file raw cache).
    """
    html = push(record(item("Nə vaxt bağlanır?", "<p>Ayın sonunda.</p>")))
    dom = [Block("Nə vaxt bağlanır?", "p")]  # question rendered server-side; answer is not

    assert faq_blocks(html, dom, PATH) == [Block("Nə vaxt bağlanır? Ayın sonunda.", FAQ_TAG)]


def test_faq_item_fully_rendered_in_the_dom_is_not_appended_twice() -> None:
    """The skip still has a job when a page server-renders a COMPLETE
    accordion item -- question and full answer both -- otherwise the fix
    would just delete the dedupe. Measured corpus-wide: 0/581 pairs are
    dropped by a full-question-AND-full-answer rule today (no ABB page
    currently server-renders a complete item), but the guard must still fire
    the day a page does.
    """
    html = (FIXTURES / "nagd-kredit.html").read_text(encoding="utf-8")
    dom, _ = content_blocks(html, NAGD_KREDIT)
    q, a = faq_pairs(html, NAGD_KREDIT)[0]

    seeded = [*dom, Block(q, "p"), Block(a, "p")]
    assert not [b for b in faq_blocks(html, seeded, NAGD_KREDIT) if b.text.startswith(q)]
    assert [b for b in faq_blocks(html, dom, NAGD_KREDIT) if b.text.startswith(q)]


def test_faq_answer_prefix_rendered_in_dom_is_still_recovered() -> None:
    """The DOM sometimes carries only a truncated preview of the answer -- a
    teaser sharing the opening clause -- while the full accordion answer is
    longer. A prefix-based dedupe (the shipped fix-2 rule) mistakes that
    teaser for proof the whole answer is already rendered and silently drops
    the pair: 3 real pages lost their only copy of a genuine answer this way
    (measured 2026-09-14 on the 550-file raw cache).
    This is the case that is broken today.
    """
    answer = (
        "Kreditin faiz dərəcəsi illik 24%-dən başlayır və müddətdən asılı "
        "olaraq dəyişə bilər, konkret məbləğ üçün filiala müraciət edin."
    )
    html = push(record(item("Faiz dərəcəsi nə qədərdir?", f"<p>{answer}</p>")))
    dom = [Block(answer[:60], "p")]  # DOM renders only a teaser/prefix, not the full answer

    assert faq_blocks(html, dom, PATH) == [Block(f"Faiz dərəcəsi nə qədərdir? {answer}", FAQ_TAG)]


def test_page_without_flight_payload_yields_nothing() -> None:
    assert flight_payload("<html><body><p>salam</p></body></html>") == ""
    assert faq_pairs("<html><body><p>salam</p></body></html>", PATH) == []
