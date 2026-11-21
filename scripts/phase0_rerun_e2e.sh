#!/usr/bin/env bash
cd ~/projects/multi-agent-system || exit 1
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
pkill -f "scripts/mock_llm_server.py" 2>/dev/null
sleep 2
rm -f data/agent_memory.sqlite data/agent_memory.sqlite-wal data/agent_memory.sqlite-shm
nohup ./.venv/bin/python scripts/mock_llm_server.py > logs/mock_llm.log 2>&1 &
sleep 3
echo "### mock process ###"
ps -eo pid,args | grep -a mock_llm_server | grep -v grep
echo "### running E2E ###"
timeout 320 ./.venv/bin/python scripts/phase0_e2e.py > logs/e2e.out 2>&1
echo "E2E_EXIT=$?"
