#!/bin/sh
set -e
# Apply every numbered migration in order, not just 001 -- a fresh database
# needs all of them (a clean init that only ran 001 was missing 002's `fold`
# column, so hybrid lexical retrieval 502'd on every question).
for f in /migrations/*.sql; do
  psql -v ON_ERROR_STOP=1 -v dim="$EMBEDDING_DIM" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$f"
done
