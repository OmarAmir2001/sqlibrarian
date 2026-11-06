#!/usr/bin/env bash
# Runs on the first start of a new records-db volume (after 01_schema.sql, 02_catalog.sql).
# Passes the role passwords from the container environment (set from .env) to security.psql.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v reader_password="$SQLIB_READER_PASSWORD" \
    -v approver_password="$SQLIB_APPROVER_PASSWORD" \
    -v checker_password="$SQLIB_CHECKER_PASSWORD" \
    -f /docker-entrypoint-initdb.d/security.psql
