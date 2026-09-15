// apps/web/src/App.tsx
//
// Scaffold shell only (Task 26). The Data/Chat/Analytics screens and the
// SourceLedger/StageProgress/StatTiles components land in later tasks; this
// just lays out the conversation column + right-rail ledger the theme is
// built around, per SPEC.md §11.4.
export default function App() {
  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <main className="max-w-measure" style={{ flex: 1, padding: "1.5rem" }}>
        <h1>ABB Assistant</h1>
        <p>Reviewer UI scaffold.</p>
      </main>
      <aside
        aria-label="Source ledger"
        style={{ width: "320px", borderLeft: "1px solid var(--rule)" }}
      />
    </div>
  );
}
