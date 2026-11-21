#!/usr/bin/env bash
# Default: one Coordinator, no pre-mounted workers or MCP daemons.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
MOCK=0
LEGACY=0
for arg in "$@"; do
  case "$arg" in
    --mock) MOCK=1 ;;
    --legacy) LEGACY=1 ;;
    *) echo "Usage: bash scripts/start_coordinator.sh [--mock] [--legacy]" >&2; exit 2 ;;
  esac
done
export MAS_COORDINATOR_ONLY=$((1 - LEGACY))
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Missing .venv; run uv sync first." >&2
  exit 1
fi
mkdir -p logs data mcp_workspace/sandboxes
if curl -fsS --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1; then
  echo "Port 8000 already serves an API. Stop it before changing startup modes." >&2
  exit 1
fi

wait_for_health() {
  local port="$1" pid="$2"
  for ((i=0; i<50; i++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Service on $port exited; inspect logs." >&2
      return 1
    fi
    if curl -fsS --max-time 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done
  echo "Service on $port did not become healthy." >&2
  return 1
}

started=()
cleanup_failed_start() {
  for pid in "${started[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup_failed_start ERR
if [[ "$MOCK" == 1 ]]; then
  export OPENAI_API_KEY=mock OPENAI_BASE_URL=http://127.0.0.1:9100/v1 OPENAI_MODEL=mock
  if ! curl -fsS --max-time 2 http://127.0.0.1:9100/health >/dev/null 2>&1; then
    nohup "$PYTHON" scripts/mock_llm_server.py >logs/mock_llm.log 2>&1 &
    started+=("$!")
    wait_for_health 9100 "$!"
  fi
fi
if [[ "$LEGACY" == 1 ]]; then
  echo "Explicit legacy mode: mounting research, coding and analysis."
  nohup "$PYTHON" -m agent_ecosystem.mcp_calculator >logs/calculator.log 2>&1 &
  started+=("$!")
  for entry in research:8001 coding:8002 analysis:8003; do
    name="${entry%:*}"; port="${entry#*:}"
    nohup "$PYTHON" -m agent_ecosystem.a2a_server --name "$name" --port "$port" >"logs/$name.log" 2>&1 &
    started+=("$!")
    wait_for_health "$port" "$!"
  done
fi
nohup "$PYTHON" -m agent_ecosystem.coordinator --host 127.0.0.1 --port 8000 >logs/coordinator.log 2>&1 &
pid="$!"
started+=("$pid")
wait_for_health 8000 "$pid"
trap - ERR
echo "Coordinator started (pid=$pid)."
echo "Dashboard: http://127.0.0.1:8000/dashboard"
echo "Coordinator Skills: Write_Skills, Register Tools"
if [[ "$LEGACY" == 0 ]]; then echo "No base agents mounted. Create a group from the Dashboard."; fi
