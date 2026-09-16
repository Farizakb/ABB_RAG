// apps/web/src/screens/Analytics.tsx   SPEC.md §11.3
//
// Table-first: the two tables (top sources, every question) are the record
// of truth; the three charts above them only illustrate what the tables
// already contain. `window` is always the literal "7d" here — never a
// free-form string — because the server 422s anything that doesn't match
// ^\d{1,3}d$ (services/chat/app/routes.py).
import { useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, type AnalyticsSummary, type InteractionRow } from "../api";
import { StatTiles } from "../components/StatTiles";

const INK = "#16202B", ACCENT = "#0B5FA5", OK = "#1F7A5C", FLAG = "#A23B2E", RULE = "#CFD6DE";

export function Analytics() {
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [rows, setRows] = useState<InteractionRow[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.analytics("7d").then((s) => { setSummary(s); setError(""); }).catch((e) => {
      setError(e instanceof Error ? e.message : "Failed to load analytics summary.");
    });
  }, []);

  useEffect(() => {
    // 200 is the server's own ceiling (services/chat/app/routes.py clamps
    // limit into [1, 200]) -- the highest count "Every question asked" can
    // honestly promise without a second page.
    api.interactions(q, 200).then((r) => {
      setRows(r.items);
      setTotal(r.total);
      setError("");
    }).catch((e) => {
      setError(e instanceof Error ? e.message : "Failed to load interactions.");
    });
  }, [q]);

  // Alert renders above content rather than replacing it, so a transient
  // failure (e.g. a search keystroke) doesn't blank panels already loaded.
  return (
    <section>
      {error && <p role="alert" style={{ color: "var(--flag)" }}>{error}</p>}
      {!summary ? <p>Loading…</p> : <>
      <StatTiles totals={summary.totals} />

      <h2>Questions over time</h2>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={summary.volume_by_day}>
          <CartesianGrid stroke={RULE} vertical={false} />
          <XAxis dataKey="day" stroke={INK} /><YAxis stroke={INK} allowDecimals={false} />
          <Tooltip />
          <Line type="monotone" dataKey="answered" stroke={ACCENT} dot={false} />
        </LineChart>
      </ResponsiveContainer>

      {/* Split by refusal class, which is what ties this screen back to R7
          and proves the grounding contract is real. refused_unsafe is part
          of the same series but never appears in the demo seed (both
          refusal classes deliberately seeded are out_of_scope and advisory
          -- scripts/seed_demo.py), so it isn't plotted as its own bar here. */}
      <h2>Answered versus refused</h2>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={summary.volume_by_day}>
          <CartesianGrid stroke={RULE} vertical={false} />
          <XAxis dataKey="day" stroke={INK} /><YAxis stroke={INK} allowDecimals={false} />
          <Tooltip /><Legend />
          <Bar dataKey="answered" stackId="a" fill={OK} />
          <Bar dataKey="refused_out_of_scope" stackId="a" fill={RULE} />
          <Bar dataKey="refused_advisory" stackId="a" fill={FLAG} />
        </BarChart>
      </ResponsiveContainer>

      {/* Windowed to the same 7d as the charts and tiles above, not all time. */}
      <h2>Most-cited ABB pages</h2>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <tbody>
          {summary.top_sources.map((s) => (
            <tr key={s.url} className="ledger-row">
              <td><a href={s.url} target="_blank" rel="noreferrer">{s.url}</a></td>
              <td className="num">{s.count}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Every question asked</h2>
      <p className="num" style={{ color: "var(--rule)" }}>{rows.length} of {total}</p>
      <input aria-label="Search questions" value={q} onChange={(e) => setQ(e.target.value)}
             placeholder="Search" />
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr className="ledger-row">
            <th align="left">Time</th><th align="left">Question</th>
            <th align="left">Outcome</th><th align="right">Latency</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="ledger-row">
              <td className="num">{new Date(r.created_at).toLocaleString()}</td>
              <td>{r.question}</td>
              <td>{r.refused ? `refused (${r.refusal_class})` : "answered"}</td>
              <td className="num">{r.latency_ms} ms</td>
            </tr>
          ))}
        </tbody>
      </table>
      </>}
    </section>
  );
}
