#!/bin/sh
set -e
psql -v ON_ERROR_STOP=1 -v dim="$EMBEDDING_DIM" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /migrations/001_schema.sql
