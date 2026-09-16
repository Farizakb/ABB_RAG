// apps/web/src/screens/Chat.tsx
//
// Ruling P112: the answer shape is api.ts's QuestionResponse, not a locally
// redefined mirror.
import { useRef, useState } from "react";
import { api, type QuestionResponse } from "../api";
import { SourceLedger } from "../components/SourceLedger";

// Ruling P113: every RefusalClass gets its own line — unsafe must not fall
// into the out_of_scope copy.
const REFUSAL_LABELS: Record<string, string> = {
  advisory: "Personal assessment — routed to a human channel",
  unsafe: "Cannot help with this request",
  out_of_scope: "Not in ABB's published information",
};

export function Chat({ corpusId }: { corpusId: string }) {
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<"idle" | "retrieving" | "generating">("idle");
  const [answer, setAnswer] = useState<QuestionResponse | null>(null);
  const [error, setError] = useState("");
  const session = useRef(crypto.randomUUID());

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setAnswer(null);
    setError("");
    setPhase("retrieving");
    const timer = setTimeout(() => setPhase("generating"), 300); // the measured retrieval budget
    try {
      setAnswer(await api.ask(corpusId, question, session.current));
    } catch (err) {
      // Ruling P114: a failed ask() must land in a visible error state, not
      // an unhandled rejection and a blank screen.
      setError(err instanceof Error ? err.message : "Question failed.");
    } finally {
      clearTimeout(timer);
      setPhase("idle");
    }
  }

  return (
    <div className="chat-grid">
      <section>
        <form onSubmit={submit}>
          <label htmlFor="q">Ask about ABB</label>
          <input id="q" value={question} onChange={(e) => setQuestion(e.target.value)}
                 placeholder="Nağd kredit üzrə maksimum məbləğ nə qədərdir?" />
          <button type="submit" className="primary" disabled={phase !== "idle" || !question}>Ask</button>
        </form>

        {phase !== "idle" && <p aria-live="polite" className="status">{phase}…</p>}
        {error && <p role="alert">{error}</p>}

        {answer && (
          <article aria-live="polite"
                   style={{ borderLeft: `3px solid ${answer.refused ? "var(--rule)" : "var(--accent)"}`,
                            paddingLeft: "1rem" }}>
            {answer.refused && (
              <p style={{ color: "var(--rule)" }}>
                {REFUSAL_LABELS[answer.refusal_class ?? "out_of_scope"]}
              </p>
            )}
            <p>{answer.answer}</p>
            {/* No link here. §11.2's path back to abb-bank.az is the ledger's
                footer row — one link surface, right beside the provenance. */}
            <p style={{ fontSize: "0.8rem" }} className="meta">
              {answer.timings_ms.retrieval_ms}ms retrieval ·{" "}
              {answer.timings_ms.generation_ms}ms generation ·{" "}
              {answer.timings_ms.total_ms}ms total
            </p>
          </article>
        )}
      </section>

      {/* Rendered only once an answer exists, so the empty ledger and its
          footer never appear on an idle screen. */}
      {answer && <SourceLedger sources={answer.sources} insufficient={answer.refused} />}
    </div>
  );
}
