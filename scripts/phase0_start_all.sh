#!/usr/bin/env bash
# Phase 0: clean start of all 5 services + verification (no E2E here).
cd ~/projects/multi-agent-system || exit 1
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1

echo "### stopping any existing services ###"
pkill -f "agent_ecosystem.a2a_server" 2>/dev/null
pkill -f "scripts/mock_llm_server.py" 2>/dev/null
pkill -f "agent_ecosystem.mcp_calculator" 2>/dev/null
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
sleep 7

echo "### process check ###"
ps -eo pid,args | grep -aE "mock_llm_server|mcp_calculator|a2a_server" | grep -v grep

echo "### agent health ###"
for p in 8001 8002 8003; do
  printf 'port %s: ' "$p"
  curl -s --max-time 5 "http://127.0.0.1:${p}/health" || printf 'NO-RESPONSE'
  echo
done

echo "### mock llm probe ###"
curl -s --max-time 5 -X POST http://127.0.0.1:9100/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"system","content":"You are Analysis Agent."},{"role":"user","content":"hi"}],"tools":[],"tool_choice":"none"}' \
  | head -c 160
echo
echo "START_ALL_DONE"
