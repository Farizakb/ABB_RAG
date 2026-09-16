# scripts/ingest_fixture.py
"""Ingest a corpus artifact and wait for `ready`. Used by `make demo`, by the
fresh-clone test, and by CI."""

from __future__ import annotations

import json
import pathlib
import sys
import time
from typing import Any

import httpx

RAG = "http://localhost:8080"


def main() -> int:
    corpus = json.loads(pathlib.Path(sys.argv[1]).read_text("utf-8"))
    with httpx.Client(timeout=120.0) as client:
        cid = client.post(f"{RAG}/api/v1/corpora", json={"corpus": corpus}).json()["corpus_id"]
        status: dict[str, Any] = {}
        for _ in range(90):
            resp = client.get(f"{RAG}/api/v1/corpora/{cid}")
            # `rag`'s corpora row is written by the background ingest task, not
            # the upload request itself, so an immediate poll can still race a
            # 404 "unknown corpus" -- treat that (and any other non-200) as
            # not-ready-yet rather than a missing "stage" key crashing the poll.
            status = resp.json() if resp.status_code == 200 else {}
            if status.get("stage") in {"ready", "failed"}:
                break
            time.sleep(2)
    print(cid if status.get("stage") == "ready" else f"FAILED: {status.get('error')}")
    return 0 if status.get("stage") == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
