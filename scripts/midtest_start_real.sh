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

echo "=== stopping old services ==="
pkill -f "agent_ecosystem" 2>/dev/null || true
pkill -f "scripts/mock_llm_server" 2>/dev/null || true
sleep 2

mkdir -p logs
echo "=== calculator MCP (9001) ==="
nohup ./.venv/bin/python -m agent_ecosystem.mcp_calculator > logs/calculator.log 2>&1 &
sleep 2

echo "=== base agents (8001-8003) ==="
nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name research --port 8001 > logs/research.log 2>&1 &
nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name coding   --port 8002 > logs/coding.log 2>&1 &
nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name analysis --port 8003 > logs/analysis.log 2>&1 &
sleep 4

echo "=== coordinator (8000) ==="
nohup ./.venv/bin/python -m agent_ecosystem.coordinator --host 0.0.0.0 --port 8000 > logs/coordinator.log 2>&1 &
sleep 5

echo "=== health checks ==="
for port in 8000 8001 8002 8003; do
  printf "  port %s: " "$port"
  curl -s --max-time 5 "http://127.0.0.1:${port}/health" 2>/dev/null || printf "FAIL"
  echo
done
curl -s --max-time 3 http://127.0.0.1:9100/health >/dev/null 2>&1 \
  && echo "  WARNING: mock LLM (9100) is RUNNING — real-mode contamination risk" \
  || echo "  mock LLM (9100): not running (expected for real mode)"
echo "REAL-MODEL SERVICES STARTED"
