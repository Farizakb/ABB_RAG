// frontend/src/api.ts
//
// Types mirror backend/shared/shared/contracts.py field-for-field.
// AnalyticsSummary and InteractionsResponse mirror
// backend/chat/chat/analytics.py's summary()/interactions() return shapes
// field-for-field instead, since those two aren't Pydantic contracts.

export type SourceClass = "product" | "campaign" | "corporate" | "stub" | "volatile" | "index";
export type RefusalClass = "out_of_scope" | "advisory" | "unsafe";
export type Stage = "validating" | "embedding" | "indexing" | "ready" | "failed";

export type Fact = {
  attribute: string;
  value_num?: number | null;
  value_text?: string | null;
  unit?: string | null;
  currency?: string | null;
  raw_fragment: string;
  source_url?: string | null;
};

export type Document = {
  url: string;
  canonical_url?: string | null;
  title: string;
  section_path: string[];
  source_class: SourceClass;
  lang: string;
  status?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
  text: string;
  facts: Fact[];
  content_hash: string;
  fetched_at?: string | null;
};

export type Corpus = {
  corpus_version: string;
  source: Record<string, unknown>;
  scraped_at?: string | null;
  stats: Record<string, number>;
  documents: Document[];
};

export type CorpusStatus = {
  corpus_id: string;
  status: string;
  stage: Stage;
  doc_count: number;
  chunk_count: number;
  fact_count: number;
  dropped: Record<string, unknown>[];
  token_stats: Record<string, number>;
  embedding_model?: string | null;
  error?: string | null;
};

export type Source = {
  n: number;
  title: string;
  section_path: string[];
  url: string;
  listing_url?: string | null;
  score: number;
  source_class: SourceClass;
  facts: Fact[];
};

export type QuestionResponse = {
  interaction_id: string;
  answer: string;
  sources: Source[];
  grounded: boolean;
  refused: boolean;
  refusal_class?: RefusalClass | null;
  timestamp: string;
  timings_ms: Record<string, number>;
};

export type VolumeDay = {
  day: string;
  answered: number;
  refused_out_of_scope: number;
  refused_advisory: number;
  // Included by analytics.py's VOLUME query even though the demo seed
  // (scripts/seed_demo.py) never produces an unsafe refusal — kept so the
  // type doesn't silently drop a real count if one ever occurs.
  refused_unsafe: number;
};

export type TopSource = { url: string; count: number };

export type AnalyticsTotals = {
  questions: number;
  median_latency_ms: number;
  grounded_rate: number;
  cost_usd: number;
};

export type AnalyticsSummary = {
  volume_by_day: VolumeDay[];
  grounded_rate: number;
  refusal_rate: number;
  // A small-talk reply (grounded=false, refused=false) is neither
  // grounded nor refused -- its own count, kept out of grounded_rate and
  // refusal_rate so a friendly greeting never reads as a miss.
  small_talk_count: number;
  // Windowed by the same `window` param as volume_by_day/totals, not all-time.
  top_sources: TopSource[];
  totals: AnalyticsTotals;
};

export type InteractionRow = {
  id: string;
  created_at: string;
  question: string;
  answer: string;
  grounded: boolean;
  refused: boolean;
  refusal_class?: RefusalClass | null;
  latency_ms: number;
};

export type InteractionsResponse = { items: InteractionRow[]; total: number };

const json = async (r: Response) => {
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  return r.json();
};

export const api = {
  uploadCorpus: (corpus: Corpus): Promise<CorpusStatus> =>
    fetch("/api/v1/corpora", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ corpus }) }).then(json),
  corpusStatus: (id: string): Promise<CorpusStatus> => fetch(`/api/v1/corpora/${id}`).then(json),
  ask: (corpus_id: string, question: string, session_id: string): Promise<QuestionResponse> =>
    fetch("/api/v1/questions", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ corpus_id, question, session_id }) }).then(json),
  analytics: (window = "7d"): Promise<AnalyticsSummary> =>
    fetch(`/api/v1/analytics/summary?window=${window}`).then(json),
  interactions: (q = "", limit = 50): Promise<InteractionsResponse> =>
    fetch(`/api/v1/interactions?q=${encodeURIComponent(q)}&limit=${limit}`).then(json),
};
