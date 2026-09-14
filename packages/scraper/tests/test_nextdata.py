# packages/scraper/tests/test_nextdata.py
# ruff: noqa: RUF002 -- this file quotes a lot of genuine Azerbaijani
# fixture/assertion text (dotless-i and friends); it isn't ambiguous, it's AZ.
import json
from pathlib import Path

from abb_scraper.extract import Block, content_blocks, extract_page, faq_blocks
from abb_scraper.nextdata import faq_pairs, flight_payload

FIXTURES = Path("fixtures/raw")


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


def test_nagd_kredit_faq_is_recovered_and_is_not_in_the_dom():
    """The page the whole extraction pipeline was derived from carries a
    "Nağd kredit haqqında sual-cavab" accordion that never reaches the DOM.
    """
    html = (FIXTURES / "nagd-kredit.html").read_text(encoding="utf-8")
    pairs = faq_pairs(html)
    questions = {q for q, _ in pairs}

    assert len(pairs) >= 20
    assert "Nağd kredit nədir?" in questions
    # Flight-only: the served markup has the accordion's data but not its text.
    body = html.split("__next_f")[0]
    assert "Nağd kredit nədir?" not in body

    answer = next(a for q, a in pairs if q == "Nağd kredit nədir?")
    assert "kredit" in answer.lower()
    assert "<" not in answer


def test_item_split_across_two_pushes_is_recovered():
    """A single flight row is routinely cut in half between two pushes; the
    payload must be concatenated before it is scanned.
    """
    row = item("Kredit nədir?", "<p>Bank vəsaitidir.</p>")
    cut = row.index("dir?")
    assert faq_pairs(push(row[:cut], row[cut:])) == [("Kredit nədir?", "Bank vəsaitidir.")]


def test_referenced_answer_is_sliced_by_utf8_bytes_not_characters():
    """`$36` answers point at a `36:T<hex>,` row whose length is in UTF-8
    bytes. Azerbaijani text has more bytes than characters, so a character
    slice over-reads into whatever the stream emits next.
    """
    body = "<p>Şərtlər əlverişlidir.</p>"
    row = f"36:T{len(body.encode()):x},{body}\n37:TRAILING-JUNK"
    html = push(item("Şərtlər", "$36") + "\n" + row)

    assert faq_pairs(html) == [("Şərtlər", "Şərtlər əlverişlidir.")]


def test_reference_row_not_at_line_start_is_still_resolved():
    """Observed on biznes/**/kocurmeler: the referenced row follows the
    previous row's body on the same line (`...</p>3a:Ta6c,<p>...`).
    """
    body = "<p>Sened teleb olunur.</p>"
    row = f"3a:T{len(body.encode()):x},{body}"
    html = push(item("Məlumat", "$3a") + "</p>" + row)
    assert faq_pairs(html) == [("Məlumat", "Sened teleb olunur.")]


def test_placeholder_unresolvable_and_duplicate_items_are_dropped():
    html = push(
        ",".join(
            [
                item("Bura metn yazilmalidir", "<p>Bura metn yazilmalidir</p>"),
                item("Yoxdur", "$99"),
                item("Qiymət?", "<p>5 AZN</p>"),
                item("Qiymət?", "<p>5 AZN</p>"),
            ]
        )
    )
    assert faq_pairs(html) == [("Qiymət?", "5 AZN")]


def test_word_split_across_inline_spans_is_not_shattered():
    """Much of this content is pasted from Word, which wraps single
    Azerbaijani characters in their own <span>. A per-text-node separator
    turns "çox" into "ç ox".
    """
    html = push(item("Sual?", "<p><span>ç</span>ox hallarda</p><p>ikinci</p>"))
    assert faq_pairs(html) == [("Sual?", "çox hallarda ikinci")]


def test_extract_page_carries_the_faq_into_the_document_text():
    html = (FIXTURES / "nagd-kredit.html").read_text(encoding="utf-8")
    page = extract_page(html, "https://abb-bank.az/ferdi/kreditler/nagd-kredit")

    q, a = next((q, a) for q, a in faq_pairs(html) if q == "Nağd kredit nədir?")
    assert f"{q} {a}" in page.text
    # Question and answer share one line: `chunk_document` splits on line
    # boundaries and must not be able to strand a question from its answer.
    assert any(q in line and a in line for line in page.text.split("\n"))


def test_faq_item_already_rendered_in_the_dom_is_not_appended_twice():
    html = (FIXTURES / "nagd-kredit.html").read_text(encoding="utf-8")
    dom, _ = content_blocks(html, "/ferdi/kreditler/nagd-kredit")
    q, _ = faq_pairs(html)[0]

    assert not [b for b in faq_blocks(html, [*dom, Block(q, "p")]) if b.text.startswith(q)]
    assert [b for b in faq_blocks(html, dom) if b.text.startswith(q)]


def test_page_without_flight_payload_yields_nothing():
    assert flight_payload("<html><body><p>salam</p></body></html>") == ""
    assert faq_pairs("<html><body><p>salam</p></body></html>") == []
