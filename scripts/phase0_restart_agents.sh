#!/usr/bin/env bash
# Restart the three A2A agents (picks up a2a_server.py changes); keep mock LLM + calculator.
cd ~/projects/multi-agent-system || exit 1
pkill -f "agent_ecosystem.a2a_server" 2>/dev/null
sleep 2
# reset runtime memory DB for a clean E2E trace (gitignored data)
rm -f data/agent_memory.sqlite data/agent_memory.sqlite-wal data/agent_memory.sqlite-shm
(nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name research --port 8001 > logs/research.log 2>&1 &)
(nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name coding --port 8002 > logs/coding.log 2>&1 &)
(nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name analysis --port 8003 > logs/analysis.log 2>&1 &)
sleep 6
for p in 8001 8002 8003; do
  printf 'port %s: ' "$p"
  curl -s --max-time 5 "http://127.0.0.1:${p}/health" || printf 'NO-RESPONSE'
  echo
done
echo "RESTART_DONE"
