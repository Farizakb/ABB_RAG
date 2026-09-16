// apps/web/src/App.tsx
//
// Scaffold shell (Task 26), mounting Data (Task 27), Chat (Task 28) and
// Analytics (Task 30). Chat lays out its own conversation column +
// right-rail SourceLedger per SPEC.md §11.4, so this shell no longer
// carries a placeholder <aside> — keeping one here would duplicate the
// ledger Chat already renders.
import { useCallback, useState } from "react";
import { Data } from "./screens/Data";
import { Chat } from "./screens/Chat";
import { Analytics } from "./screens/Analytics";

export default function App() {
  const [tab, setTab] = useState<"data" | "chat" | "analytics">("data");
  const [corpusId, setCorpusId] = useState<string | null>(null);

  // useCallback: onReady sits in Data's effect deps, so a fresh function
  // identity on every App render was restarting Data's status polling.
  const onReady = useCallback((id: string) => {
    setCorpusId(id);
  }, []);

  return (
    <div style={{ minHeight: "100vh" }}>
      {/* SPEC §11.4 scopes the 68ch measure to the conversation column, not to
          the page: Data sets it on its own section, Chat carries it in the grid
          template, and Analytics is table-first and needs the full width. */}
      <main>
        <h1>ABB Assistant</h1>
        <nav>
          <button onClick={() => setTab("data")} aria-current={tab === "data" ? "page" : undefined}>
            Data
          </button>
          <button onClick={() => setTab("chat")} disabled={!corpusId}
                  aria-current={tab === "chat" ? "page" : undefined}>
            Chat
          </button>
          {/* Not gated on corpusId: it reads the stored interaction record
              (Task 25's aggregations), which is independent of whatever
              corpus this browser session has loaded. */}
          <button onClick={() => setTab("analytics")}
                  aria-current={tab === "analytics" ? "page" : undefined}>
            Analytics
          </button>
        </nav>
        {tab === "data" && <Data onReady={onReady} />}
        {tab === "chat" && corpusId && <Chat corpusId={corpusId} />}
        {tab === "analytics" && <Analytics />}
      </main>
    </div>
  );
}
