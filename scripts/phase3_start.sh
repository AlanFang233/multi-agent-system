#!/usr/bin/env bash
# Phase 3 full start: stops old instances, starts 6 services, verifies health.
set -e
cd ~/projects/multi-agent-system
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1

echo "=== stopping old services ==="
pkill -f "agent_ecosystem" 2>/dev/null || true
pkill -f "scripts/mock_llm_server" 2>/dev/null || true
sleep 2

echo "=== mock LLM (9100) + calculator (9001) ==="
nohup ./.venv/bin/python scripts/mock_llm_server.py > logs/mock_llm.log 2>&1 &
nohup ./.venv/bin/python -m agent_ecosystem.mcp_calculator > logs/calculator.log 2>&1 &
sleep 3

echo "=== agents (8001-8003) ==="
nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name research --port 8001 > logs/research.log 2>&1 &
nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name coding   --port 8002 > logs/coding.log 2>&1 &
nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name analysis --port 8003 > logs/analysis.log 2>&1 &
sleep 4

echo "=== coordinator (8000) ==="
nohup ./.venv/bin/python -m agent_ecosystem.coordinator --host 0.0.0.0 --port 8000 > logs/coordinator.log 2>&1 &
sleep 5

echo ""
echo "=== health checks ==="
for port in 8000 8001 8002 8003; do
  printf "  port %s: " "$port"
  curl -s --max-time 5 "http://127.0.0.1:${port}/health" 2>/dev/null || printf "FAIL"
  echo
done

echo ""
echo "=== dashboard ==="
DASH_CODE=$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/dashboard 2>/dev/null)
echo "  /dashboard HTTP ${DASH_CODE} ($(curl -s --max-time 10 http://127.0.0.1:8000/dashboard 2>/dev/null | wc -c) bytes)"

echo ""
echo "=== listener check ==="
ss -ltnp 2>/dev/null | grep -E ':(8000|8001|8002|8003|9100|9001)' || true

echo ""
echo "ALL SERVICES STARTED"
echo "Dashboard: http://127.0.0.1:8000/dashboard"