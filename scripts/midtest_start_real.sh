#!/usr/bin/env bash
# Mid_Test REAL-model start.
# - Loads HKGAI credentials from .env.hkgai.bak into ENV VARS (pydantic-settings
#   precedence: env vars override .env), so the on-disk .env (Mock) is untouched.
# - The Mock LLM server is deliberately NOT started: anything accidentally
#   pointing at 9100 fails loudly instead of silently polluting real results.
# - The API key is never printed.
set -e
cd ~/projects/multi-agent-system || exit 2
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1

while IFS='=' read -r k v; do
  case "$k" in ''|\#*) continue ;; OPENAI_*) ;; *) continue ;; esac
  v="$(printf '%s' "$v" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  export "$k=$v"
done < .env.hkgai.bak
echo "LLM backend: ${OPENAI_BASE_URL} model=${OPENAI_MODEL} (key loaded, not shown)"

echo "=== Coordinator only (workers are created from Group requests) ==="
bash scripts/start_coordinator.sh "$@"
curl -s --max-time 3 http://127.0.0.1:9100/health >/dev/null 2>&1 \
  && echo "  WARNING: mock LLM (9100) is RUNNING — real-mode contamination risk" \
  || echo "  mock LLM (9100): not running (expected for real mode)"
echo "REAL-MODEL COORDINATOR STARTED"
