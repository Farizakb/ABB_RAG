// frontend/src/storage.ts
export type Manifest = {
  corpus_id: string; pages: number; scraped_at?: string;
  quota_bytes: number; degraded: boolean; stage?: string;
};

const CORPUS = "abb.corpus";
const MANIFEST = "abb.corpus.manifest";

export function saveCorpus(corpus: { documents: unknown[] }, corpusId: string): Manifest {
  let degraded = false;
  try {
    localStorage.setItem(CORPUS, JSON.stringify(corpus));
  } catch {
    localStorage.removeItem(CORPUS);
    degraded = true; // manifest survives; the upload never fails over a storage limit
  }
  const manifest: Manifest = {
    corpus_id: corpusId, pages: corpus.documents.length,
    quota_bytes: 0, degraded,
  };
  // quota_bytes must reflect what's actually left in localStorage, but the
  // manifest entry being written is itself part of that total. Write,
  // measure the real total, and rewrite until the stored figure is correct
  // -- stabilizes in 1-2 passes since the manifest's own size only moves
  // when quota_bytes gains or loses a digit.
  for (let i = 0; i < 5; i++) {
    localStorage.setItem(MANIFEST, JSON.stringify(manifest));
    const measured = JSON.stringify(localStorage).length * 2;
    if (measured === manifest.quota_bytes) break;
    manifest.quota_bytes = measured;
  }
  return manifest;
}

export const loadManifest = (): Manifest | null => {
  const raw = localStorage.getItem(MANIFEST);
  return raw ? (JSON.parse(raw) as Manifest) : null;
};

export const loadCorpus = (): unknown | null => {
  const raw = localStorage.getItem(CORPUS);
  return raw ? JSON.parse(raw) : null;
};
