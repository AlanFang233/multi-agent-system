#!/usr/bin/env bash
# Phase 2: start all 6 services (mock LLM, calculator, 3 agents, coordinator).
cd ~/projects/multi-agent-system || exit 1
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
echo "### stopping any existing services ###"
pkill -f "agent_ecosystem.a2a_server" 2>/dev/null
pkill -f "scripts/mock_llm_server.py" 2>/dev/null
pkill -f "agent_ecosystem.mcp_calculator" 2>/dev/null
pkill -f "agent_ecosystem.coordinator" 2>/dev/null
sleep 3
echo "### reset memory db ###"
rm -f data/agent_memory.sqlite data/agent_memory.sqlite-wal data/agent_memory.sqlite-shm
echo "### starting mock llm (9100) + calculator (9001) ###"
nohup ./.venv/bin/python scripts/mock_llm_server.py > logs/mock_llm.log 2>&1 &
nohup ./.venv/bin/python -m agent_ecosystem.mcp_calculator > logs/calculator.log 2>&1 &
sleep 4
echo "### starting agents (8001/8002/8003) ###"
nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name research --port 8001 > logs/research.log 2>&1 &
nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name coding --port 8002 > logs/coding.log 2>&1 &
nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name analysis --port 8003 > logs/analysis.log 2>&1 &
sleep 4
echo "### starting coordinator (8000) ###"
nohup ./.venv/bin/python -m agent_ecosystem.coordinator --port 8000 > logs/coordinator.log 2>&1 &
sleep 5
echo "### health ###"
for p in 8000 8001 8002 8003; do
  printf 'port %s: ' "$p"
  curl -s --max-time 5 "http://127.0.0.1:${p}/health" 2>/dev/null || printf '(none)'
  echo
done
echo "START_ALL_DONE"