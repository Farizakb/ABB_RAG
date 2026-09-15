// apps/web/src/test/storage.test.ts
import { describe, expect, it, beforeEach } from "vitest";
import { loadManifest, saveCorpus } from "../storage";

const corpus = { corpus_version: "1", documents: [{ url: "https://abb-bank.az/a", title: "A" }] };

describe("localStorage", () => {
  beforeEach(() => localStorage.clear());

  it("writes the full corpus verbatim, as the brief asks", () => {
    saveCorpus(corpus as never, "abc123");
    expect(JSON.parse(localStorage.getItem("abb.corpus")!)).toEqual(corpus);
  });

  it("writes a separate manifest that drives the already-processed fast path", () => {
    saveCorpus(corpus as never, "abc123");
    expect(loadManifest()?.corpus_id).toBe("abc123");
  });

  it("keeps the manifest and drops the body when the quota is exceeded", () => {
    const original = Storage.prototype.setItem;
    let calls = 0;
    Storage.prototype.setItem = function (k: string, v: string) {
      if (k === "abb.corpus" && calls++ === 0) throw new DOMException("quota", "QuotaExceededError");
      return original.call(this, k, v);
    };
    const result = saveCorpus(corpus as never, "abc123");
    Storage.prototype.setItem = original;

    expect(result.degraded).toBe(true);
    expect(loadManifest()?.corpus_id).toBe("abc123"); // never fail an upload over a browser limit
  });

  it("reports quota consumption in UTF-16 units, not file bytes", () => {
    saveCorpus(corpus as never, "abc123");
    expect(loadManifest()!.quota_bytes).toBe(JSON.stringify(localStorage).length * 2);
  });
});
