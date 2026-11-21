#!/usr/bin/env bash
set -e
cd ~/projects/multi-agent-system
pkill -f "agent_ecosystem.coordinator" 2>/dev/null || true
sleep 2
nohup ./.venv/bin/python -m agent_ecosystem.coordinator --port 8000 > logs/coordinator.log 2>&1 &
sleep 5
printf "coordinator health: "
curl -s --max-time 10 http://127.0.0.1:8000/health
echo
rm -rf data/phase2_e2e
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv/bin/python scripts/phase2_e2e.py > data/phase2_e2e_out.txt 2>&1
echo "E2E_DONE"