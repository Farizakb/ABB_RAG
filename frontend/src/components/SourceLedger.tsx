// frontend/src/components/SourceLedger.tsx
//
// Source comes from ../api, not a local redefinition — one
// contract, not two that can drift.
import type { Source } from "../api";

const HELPLINE = "937 Məlumat Mərkəzi";

/** The ledger's footer row. One link surface, not two: every row
 *  above already links the page it cites, so a second call to action under the
 *  answer would print the same thing twice and go unread by the fifth question.
 *
 *  The href always comes from Source.listing_url — derived by `rag` from a
 *  document it actually retrieved and proved present in the corpus, never from
 *  answer text (invariant 12). The UI renders what it is given and computes
 *  nothing, which is why there is no path trimming in this file. */
function LedgerFooter({ sources, refused }: { sources: Source[]; refused?: boolean }) {
  const href = sources[0]?.listing_url ?? sources[0]?.url;
  return (
    <tfoot>
      <tr className="ledger-row">
        <td colSpan={3} className="meta">
          {!href ? (
            <>
              Call {HELPLINE}, or browse{" "}
              <a href="https://abb-bank.az" target="_blank" rel="noreferrer">abb-bank.az</a>.
            </>
          ) : (
            <>
              {refused
                ? "Closest page on abb-bank.az: "
                : "All of this comes from abb-bank.az — open "}
              <a href={href} target="_blank" rel="noreferrer">{href.replace("https://", "")}</a>
              {refused && <> · {HELPLINE}</>}
            </>
          )}
        </td>
      </tr>
    </tfoot>
  );
}

export function SourceLedger({ sources, insufficient }: {
  sources: Source[]; insufficient?: boolean;
}) {
  return (
    <aside aria-label="Sources">
      <h2 className={sources.length === 0 ? "meta" : undefined}>
        {sources.length === 0
          ? "Nothing retrieved"
          : insufficient
            ? "Retrieved, judged insufficient"
            : "Sources used"}
      </h2>
      <div className="table-scroll">
        <table>
          <tbody>
            {sources.map((s) => (
              <tr key={s.n} className="ledger-row" id={`source-${s.n}`}>
                <td className="num">[{s.n}]</td>
                <td>
                  <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a>
                  <div className="meta">{s.section_path.join(" › ")}</div>
                  {/* Index + attribute keeps the key unique even when two
                      facts on the same source share an attribute name. */}
                  {s.facts.map((f, i) => (
                    <div key={`${i}-${f.attribute}`}>
                      {f.attribute}: <strong>{f.raw_fragment}</strong>
                    </div>
                  ))}
                </td>
                <td className="num">{s.score.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
          <LedgerFooter sources={sources} refused={insufficient} />
        </table>
      </div>
    </aside>
  );
}
