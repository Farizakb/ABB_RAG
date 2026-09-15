// apps/web/src/App.tsx
//
// Scaffold shell (Task 26), now mounting the Data screen (Task 27). The
// Analytics screen and the SourceLedger/StatTiles components land in later
// tasks; this still lays out the conversation column + right-rail ledger
// the theme is built around, per SPEC.md §11.4.
import { useState } from "react";
import { Data } from "./screens/Data";

export default function App() {
  const [tab, setTab] = useState<"data" | "chat">("data");
  const [ready, setReady] = useState(false);

  function onReady() {
    setReady(true);
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <main className="max-w-measure" style={{ flex: 1, padding: "1.5rem" }}>
        <h1>ABB Assistant</h1>
        <nav style={{ display: "flex", gap: "1rem", marginBottom: "1rem" }}>
          <button onClick={() => setTab("data")} aria-current={tab === "data" ? "page" : undefined}>
            Data
          </button>
          <button onClick={() => setTab("chat")} disabled={!ready}
                  aria-current={tab === "chat" ? "page" : undefined}>
            Chat
          </button>
        </nav>
        {tab === "data" && <Data onReady={onReady} />}
        {tab === "chat" && <p>Chat coming in Task 28.</p>}
      </main>
      <aside
        aria-label="Source ledger"
        style={{ width: "320px", borderLeft: "1px solid var(--rule)" }}
      />
    </div>
  );
}
