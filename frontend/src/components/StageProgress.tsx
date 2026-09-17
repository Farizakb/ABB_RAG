// frontend/src/components/StageProgress.tsx
const STAGES = ["validating", "embedding", "indexing", "ready"] as const;

export function StageProgress({ stage, doc_count, chunk_count, fact_count, embedding_model }: {
  stage: string; doc_count: number; chunk_count: number;
  fact_count: number; embedding_model?: string | null;
}) {
  const at = STAGES.indexOf(stage as (typeof STAGES)[number]);
  return (
    <ol aria-label="Ingest progress" style={{ listStyle: "none", padding: 0 }}>
      {STAGES.map((s, i) => (
        <li key={s} className="ledger-row" aria-current={i === at ? "step" : undefined}>
          <span style={{ color: i <= at ? "var(--ink)" : "var(--rule)" }}>{s}</span>
        </li>
      ))}
      <li>
        <span className="num">{doc_count}</span> documents,{" "}
        <span className="num">{chunk_count}</span> chunks,{" "}
        <span className="num">{fact_count}</span> governed facts
        {embedding_model && <> · {embedding_model}</>}
      </li>
    </ol>
  );
}
