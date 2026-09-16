# packages/scraper/Dockerfile
# Build context is the repo root (see docker-compose.yml's `scraper` service):
# abb_scraper depends on the sibling `contracts` package, so both are copied in.
FROM python:3.12.7-slim

WORKDIR /app

COPY packages/contracts /app/packages/contracts
COPY packages/scraper /app/packages/scraper

RUN pip install --no-cache-dir -e /app/packages/contracts -e /app/packages/scraper

ENTRYPOINT ["abb-scrape"]
