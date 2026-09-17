// frontend/src/components/StatTiles.tsx
import type { AnalyticsTotals } from "../api";

export function StatTiles({ totals }: { totals: AnalyticsTotals }) {
  const tiles: [string, string][] = [
    ["Questions", totals.questions.toString()],
    ["Median latency", `${totals.median_latency_ms} ms`],
    ["Grounded rate", `${(totals.grounded_rate * 100).toFixed(1)}%`],
    ["Total cost", `$${totals.cost_usd.toFixed(4)}`],
  ];
  return (
    <div className="stat-tiles">
      {tiles.map(([label, value]) => (
        <div key={label} className="stat-tile">
          <div className="meta">{label}</div>
          <div className="num" style={{ fontSize: "1.6rem" }}>{value}</div>
        </div>
      ))}
    </div>
  );
}
