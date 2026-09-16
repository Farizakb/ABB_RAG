.PHONY: scrape up down ingest demo eval test lint fresh
scrape:  ; docker compose --profile scraper run --rm scraper --max-pages 400
up:      ; docker compose up -d --build
down:    ; docker compose down
ingest:  ; python scripts/ingest_fixture.py fixtures/corpus_sample.json
demo:
	$(MAKE) up
	@echo "waiting for health…" && sleep 8
	@CID=$$(python scripts/ingest_fixture.py fixtures/corpus_sample.json) && \
	 python scripts/seed_demo.py $$CID && \
	 echo "\nOpen http://localhost:8080 — corpus $$CID is ready with seeded history."
# `rag` is the only container holding OPENAI_API_KEY and the only one that can
# reach Postgres by its compose hostname (`db`) -- psycopg from the host hits
# psycopg_pool PoolTimeout even with a correct DSN (P132). So the golden set
# is copied into `rag` and the runner executes there; the host only needs
# httpx to derive/ingest the corpus id first, the same dependency `make demo`
# already has.
eval:
	@CID=$$(python scripts/ingest_fixture.py fixtures/corpus_sample.json) && \
	 docker compose cp evals rag:/tmp/evals && \
	 docker compose exec -T -e PYTHONPATH=/app -w /tmp rag \
	   python evals/runner.py --golden evals/golden.jsonl --corpus-id $$CID --out /tmp/report.md && \
	 docker compose cp rag:/tmp/report.md evals/report.md
# services/rag/app and services/chat/app are both top-level package `app`, so a
# single bare pytest run can't import both. Run per project instead, and skip
# any project a later task hasn't created yet rather than going red.
test:
	if [ -d packages ]; then pytest packages; fi
	if [ -d services/rag ]; then PYTHONPATH=packages/contracts pytest services/rag; fi
	if [ -d services/chat ]; then PYTHONPATH=packages/contracts pytest services/chat; fi
	if [ -d evals ]; then PYTHONPATH=services/rag:packages/contracts pytest evals; fi
	if [ -f apps/web/package.json ]; then cd apps/web && npm test -- --run; fi
# mypy in two calls for the same reason as `test` above: services/rag/app and
# services/chat/app are both top-level package `app`, so one bare `mypy`
# invocation (which type-checks pyproject.toml's [tool.mypy] `files` list as
# a single run) hits mypy's duplicate-module-name error.
lint:    ; ruff format --check . && ruff check . && mypy scripts packages services/rag && mypy services/chat
fresh:   ; docker compose down -v && $(MAKE) up
