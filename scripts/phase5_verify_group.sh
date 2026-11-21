#!/usr/bin/env bash
cd ~/projects/multi-agent-system
export PYTHONIOENCODING=utf-8

echo "=== restart services ==="
bash scripts/phase3_start.sh > /dev/null 2>&1
sleep 4

echo ""
echo "=== 1. Create dev team group ==="
./.venv/bin/python -m agent_ecosystem.cli group-create dev-squad "Dev Squad" --scenario dev-team

echo ""
echo "=== 2. List groups ==="
curl -s http://127.0.0.1:8000/groups | python3 -m json.tool 2>/dev/null | head -15

echo ""
echo "=== 3. Activate group (start agents) ==="
./.venv/bin/python -m agent_ecosystem.cli group-activate dev-squad

echo ""
echo "=== 4. Show group ==="
./.venv/bin/python -m agent_ecosystem.cli group-show dev-squad

echo ""
echo "=== 5. Send message to group ==="
./.venv/bin/python -m agent_ecosystem.cli group-message dev-squad "Research key RAG components and summarize."

echo ""
echo "=== 6. Original Phase 0 E2E ==="
rm -f data/agent_memory.sqlite-wal data/agent_memory.sqlite-shm
PYTHONIOENCODING=utf-8 ./.venv/bin/python scripts/phase0_e2e.py 2>&1 | grep -a 'E2E_RESULT\|final task state'

echo ""
echo "VERIFY_COMPLETE"