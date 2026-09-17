.PHONY: setup scrape up down ingest demo eval eval-mock test lint reset-data logs ps db-ui
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
db-ui:   ; docker compose --profile tools up -d --wait pgadmin
ingest:  ; python scripts/ingest_fixture.py data/corpus_sample.json
demo:
	docker compose up -d --build --wait
	@CID=$$(python scripts/ingest_fixture.py data/corpus_sample.json) && \
	 python scripts/seed_demo.py $$CID && \
	 echo "Open http://localhost:8080 - corpus $$CID is ready with seeded history."
# `rag` is the only container holding OPENAI_API_KEY and the only one that can
# reach Postgres by its compose hostname (`db`) -- psycopg from the host hits
# psycopg_pool PoolTimeout even with a correct DSN. So the golden set is
# copied into `rag` and the runner executes there; the host only needs httpx
# to derive/ingest the corpus id first, the same dependency `make demo`
# already has.
# Free: FakeEmbedder for the query side, evals/runner.py's fixed-answer mock
# client -- no OpenAI calls. Ingest is a no-op if the fixture is already
# ready (idempotent on corpus_id), so this costs $0 even on a fresh corpus
# that `make demo`/`make ingest` already paid to embed once.
eval-mock:
	@CID=$$(python scripts/ingest_fixture.py data/corpus_sample.json) && \
	 docker compose cp evals rag:/tmp/evals && \
	 MSYS_NO_PATHCONV=1 docker compose exec -T -e PYTHONPATH=/app -w /tmp rag \
	   python evals/runner.py --golden evals/golden.jsonl --corpus-id $$CID --mock --out /tmp/report.md
# Real: calls the live OpenAI API for every golden question. Costs roughly
# ten cents for the current 64-item set (see README).
eval:
	@CID=$$(python scripts/ingest_fixture.py data/corpus_sample.json) && \
	 docker compose cp evals rag:/tmp/evals && \
	 MSYS_NO_PATHCONV=1 docker compose exec -T -e PYTHONPATH=/app -w /tmp rag \
	   python evals/runner.py --golden evals/golden.jsonl --corpus-id $$CID --out /tmp/report.md && \
	 docker compose cp rag:/tmp/report.md evals/report.md
test:
	pytest
	cd frontend && npm test -- --run
lint:    ; ruff format --check . && ruff check . && mypy
# DESTROYS the Postgres volume (every ingested corpus and interaction row)
# before bringing the stack back up. No confirmation prompt. Renamed from
# `fresh` so the name itself says what it does -- never run this against a
# stack you want to keep.
reset-data:
	docker compose down -v
	$(MAKE) up
