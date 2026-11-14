#!/usr/bin/env bash
set -euo pipefail
# Relies on POSTGRES_USER and POSTGRES_DB defaults, or values from the .env file
set -a
[ ! -f .env ] || source .env
set +a
docker compose exec -T postgres psql -U "${POSTGRES_USER:-agent}" -d "${POSTGRES_DB:-multi_agent}" -c 'CREATE EXTENSION IF NOT EXISTS vector;'
