// apps/web/src/api.ts
//
// Types mirror packages/contracts/contracts/models.py field-for-field.
// Runtime response shapes for /analytics and /interactions aren't yet
// defined in contracts (chat's analytics.py ships later), so those two
// calls are typed as unknown rather than guessing a schema.

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
  analytics: (window = "7d"): Promise<unknown> => fetch(`/api/v1/analytics/summary?window=${window}`).then(json),
  interactions: (q = "", limit = 50): Promise<unknown> =>
    fetch(`/api/v1/interactions?q=${encodeURIComponent(q)}&limit=${limit}`).then(json),
};
