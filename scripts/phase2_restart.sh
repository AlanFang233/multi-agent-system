#!/usr/bin/env bash
# stop/start services (avoid nohup inside bash -lc quoting problems)
cd ~/projects/multi-agent-system || exit 1
pkill -f "agent_ecosystem" 2>/dev/null
pkill -f "scripts/mock_llm_server" 2>/dev/null
sleep 3
rm -f data/agent_memory.sqlite*
nohup ./.venv/bin/python scripts/mock_llm_server.py > logs/mock_llm.log 2>&1 &
nohup ./.venv/bin/python -m agent_ecosystem.mcp_calculator > logs/calculator.log 2>&1 &
sleep 3
nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name research --port 8001 > logs/research.log 2>&1 &
nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name coding --port 8002 > logs/coding.log 2>&1 &
nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name analysis --port 8003 > logs/analysis.log 2>&1 &
sleep 4
nohup ./.venv/bin/python -m agent_ecosystem.coordinator --port 8000 > logs/coordinator.log 2>&1 &
sleep 6
for p in 8000 8001 8002 8003; do
  printf 'port %s: ' "$p"
  curl -s --max-time 5 "http://127.0.0.1:${p}/health" 2>/dev/null || printf '(none)'
  echo
done
echo "RESTART_DONE"