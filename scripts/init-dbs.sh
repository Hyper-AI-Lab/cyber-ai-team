#!/usr/bin/env bash
# Create additional databases required by Temporal, Langfuse, etc.
# Run this once after PostgreSQL starts.

set -e

PGHOST="${POSTGRES_HOST:-localhost}"
PGPORT="${POSTGRES_PORT:-5432}"
PGUSER="${POSTGRES_USER:-cyberteam}"
PGPASSWORD="${POSTGRES_PASSWORD:-changeme-postgres-password}"

export PGPASSWORD

for DB in langfuse temporal; do
  echo "Creating database '$DB' if not exists..."
  psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -tc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1 || \
  psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -c \
    "CREATE DATABASE $DB"
done

echo "Databases ready."
