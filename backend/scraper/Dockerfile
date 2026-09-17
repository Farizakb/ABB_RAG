# backend/scraper/Dockerfile
# Build context is the repo root (see docker-compose.yml's `scraper` service):
# abb_scraper depends on the sibling `shared` package, so both are copied in.
FROM python:3.12.7-slim

WORKDIR /app

COPY backend/shared /app/backend/shared
COPY backend/scraper /app/backend/scraper

RUN pip install --no-cache-dir -e /app/backend/shared -e /app/backend/scraper

ENTRYPOINT ["abb-scrape"]
