#!/usr/bin/env bash
set -euo pipefail
python -m compileall -q src
pytest -q
curl --fail --silent http://127.0.0.1:8000/health
curl --fail --silent http://127.0.0.1:8001/.well-known/agent-card.json > /dev/null
curl --fail --silent http://127.0.0.1:8002/.well-known/agent-card.json > /dev/null
curl --fail --silent http://127.0.0.1:8003/.well-known/agent-card.json > /dev/null
printf '\nAll basic checks passed.\n'
