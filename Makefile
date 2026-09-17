.PHONY: setup scrape up down ingest demo eval eval-mock test lint reset-data db-ui logs ps
# Creates .env from .env.example if missing, then checks OPENAI_API_KEY is set
# to something other than the placeholder -- never prints the value either way.
setup:
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env from .env.example -- edit it and set OPENAI_API_KEY."; fi
	@key=$$(grep -E '^OPENAI_API_KEY=' .env | cut -d= -f2-); \
	if [ -z "$$key" ] || [ "$$key" = "sk-replace-me" ]; then \
		echo "OPENAI_API_KEY is not set in .env -- edit it and paste your key." >&2; exit 1; \
	fi
	@echo "setup OK."
scrape:  ; docker compose --profile scraper run --rm scraper --max-pages 400
up:      ; docker compose up -d --build --wait
down:    ; docker compose down
logs:    ; docker compose logs -f
ps:      ; docker compose ps
# Walkthrough tool only: loopback-bound (127.0.0.1:5050), profile-gated so it
# never starts with `up`/`demo`, and never part of the deployed stack.
db-ui:   ; docker compose --profile tools up -d pgadmin
ingest:  ; python scripts/ingest_fixture.py fixtures/corpus_sample.json
demo:
	docker compose up -d --build --wait
	@CID=$$(python scripts/ingest_fixture.py fixtures/corpus_sample.json) && \
	 python scripts/seed_demo.py $$CID && \
	 echo "Open http://localhost:8080 - corpus $$CID is ready with seeded history."
# `rag` is the only container holding OPENAI_API_KEY and the only one that can
# reach Postgres by its compose hostname (`db`) -- psycopg from the host hits
# psycopg_pool PoolTimeout even with a correct DSN (P132). So the golden set
# is copied into `rag` and the runner executes there; the host only needs
# httpx to derive/ingest the corpus id first, the same dependency `make demo`
# already has.
# Free: FakeEmbedder for the query side, evals/runner.py's fixed-answer mock
# client -- no OpenAI calls. Ingest is a no-op if the fixture is already
# ready (idempotent on corpus_id), so this costs $0 even on a fresh corpus
# that `make demo`/`make ingest` already paid to embed once.
eval-mock:
	@CID=$$(python scripts/ingest_fixture.py fixtures/corpus_sample.json) && \
	 docker compose cp evals rag:/tmp/evals && \
	 docker compose exec -T -e PYTHONPATH=/app -w /tmp rag \
	   python evals/runner.py --golden evals/golden.jsonl --corpus-id $$CID --mock --out /tmp/report.md
# Real: calls the live OpenAI API for every golden question. Costs roughly
# ten cents for the current 64-item set (see README).
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
# DESTROYS the Postgres volume (every ingested corpus and interaction row)
# before bringing the stack back up. No confirmation prompt. Renamed from
# `fresh` so the name itself says what it does -- never run this against a
# stack you want to keep.
reset-data:
	docker compose down -v
	$(MAKE) up
