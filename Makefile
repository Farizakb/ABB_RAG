.PHONY: scrape up down ingest demo eval test lint fresh
scrape:  ; docker compose --profile scraper run --rm scraper --max-pages 400
up:      ; docker compose up -d --build
down:    ; docker compose down
ingest:  ; python scripts/ingest_fixture.py fixtures/corpus_sample.json
demo:    ; $(MAKE) up && sleep 5 && $(MAKE) ingest && python scripts/seed_demo.py
eval:    ; PYTHONPATH=services/rag:packages/contracts python evals/runner.py --golden evals/golden.jsonl --out evals/report.md
# services/rag/app and services/chat/app are both top-level package `app`, so a
# single bare pytest run can't import both. Run per project instead, and skip
# any project a later task hasn't created yet rather than going red.
test:
	if [ -d packages ]; then pytest packages; fi
	if [ -d services/rag ]; then PYTHONPATH=packages/contracts pytest services/rag; fi
	if [ -d services/chat ]; then PYTHONPATH=packages/contracts pytest services/chat; fi
	if [ -d evals ]; then PYTHONPATH=services/rag:packages/contracts pytest evals; fi
	if [ -f apps/web/package.json ]; then cd apps/web && npm test -- --run; fi
lint:    ; ruff format --check . && ruff check . && mypy
fresh:   ; docker compose down -v && $(MAKE) up
