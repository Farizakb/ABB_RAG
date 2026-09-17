// frontend/src/screens/Data.tsx
import { useEffect, useState } from "react";
import { api, type Corpus, type CorpusStatus } from "../api";
import { loadCorpus, loadManifest, saveCorpus, type Manifest } from "../storage";
import { StageProgress } from "../components/StageProgress";

export function Data({ onReady }: { onReady: (id: string) => void }) {
  const [manifest, setManifest] = useState<Manifest | null>(loadManifest());
  const [status, setStatus] = useState<CorpusStatus | null>(null);
  const [error, setError] = useState("");

  async function pick(file: File) {
    setError("");
    let corpus: { documents: unknown[]; corpus_version?: string };
    try {
      corpus = JSON.parse(await file.text());
    } catch {
      return setError("That file is not valid JSON. Re-run `make scrape` and pick the output.");
    }
    if (!Array.isArray(corpus.documents) || corpus.documents.length === 0) {
      return setError("No `documents` array found. This does not look like a corpus artifact.");
    }
    // First write: the browser, before anything reaches the server.
    setManifest(saveCorpus(corpus, "pending"));
    (window as never as { __corpus: unknown }).__corpus = corpus;
  }

  async function process() {
    setError("");
    const corpus = (window as never as { __corpus: unknown }).__corpus ?? loadCorpus();
    if (!corpus) return setError("Pick a corpus file first.");
    try {
      const { corpus_id } = await api.uploadCorpus(corpus as Corpus);   // second write: the server
      setManifest(saveCorpus(corpus as { documents: unknown[] }, corpus_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
    }
  }

  useEffect(() => {
    if (!manifest?.corpus_id || manifest.corpus_id === "pending") return;
    const timer = setInterval(async () => {
      try {
        const s = await api.corpusStatus(manifest.corpus_id);
        setStatus(s);
        setError("");
        if (s.stage === "ready") { clearInterval(timer); onReady(manifest.corpus_id); }
        if (s.stage === "failed") clearInterval(timer);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Status check failed.");
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [manifest?.corpus_id, onReady]);

  // No maxWidth on the section itself: the 68ch measure is right for the
  // prose and the manifest below (reading content), but capping the whole
  // screen to it left the Data tab reading as a narrow ribbon on a wide
  // monitor. The cap stays on the reading elements only.
  return (
    <section>
      <h1>Load the ABB corpus</h1>
      <p style={{ maxWidth: "var(--measure)" }}>
        Run <code>make scrape</code> first, then pick the generated <code>corpus_*.json</code>.
      </p>

      <input type="file" accept="application/json" aria-label="Corpus file"
             onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} />

      {error && <p role="alert" style={{ maxWidth: "var(--measure)" }}>{error}</p>}

      {manifest && (
        <dl className="ledger-row" style={{ maxWidth: "var(--measure)" }}>
          <dt>Pages</dt><dd className="num">{manifest.pages}</dd>
          <dt>localStorage used</dt>
          <dd className="num">{(manifest.quota_bytes / 1e6).toFixed(2)} MB (UTF-16)</dd>
          {manifest.degraded && (
            <dd style={{ color: "var(--flag)" }}>
              Corpus body exceeded the browser quota — manifest kept, upload unaffected.
            </dd>
          )}
        </dl>
      )}

      <button onClick={process} className="primary" disabled={!manifest}>Process dataset</button>

      {status && <StageProgress {...status} />}
      {status?.stage === "ready" && <p style={{ color: "var(--ok)" }}>Ready — open Chat.</p>}
      {status?.stage === "failed" && (
        <p role="alert" style={{ maxWidth: "var(--measure)" }}>
          {status.error} — <button onClick={process}>Retry</button>
        </p>
      )}
    </section>
  );
}
