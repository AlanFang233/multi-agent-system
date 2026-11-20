#!/usr/bin/env bash
cd ~/projects/multi-agent-system || exit 2
WS=mcp_workspace/sandboxes/258c8916685642f3a261281a8ffde149
ART=artifacts/mid_test_local
PY=./.venv/bin/python
cd "$WS" || exit 2
echo "=== analytics_api.py self-check (real bookings.db) ==="
"/home/alan/projects/multi-agent-system/.venv/bin/python" analytics_api.py > "/home/alan/projects/multi-agent-system/$ART/task2_analytics_selfcheck.txt" 2>&1
echo "rc=$?"
head -40 "/home/alan/projects/multi-agent-system/$ART/task2_analytics_selfcheck.txt"
echo
echo "=== dashboard/test_contract.py (contract test vs real DB) ==="
"/home/alan/projects/multi-agent-system/.venv/bin/python" dashboard/test_contract.py > "/home/alan/projects/multi-agent-system/$ART/task2_contract_test.txt" 2>&1
echo "rc=$?"
tail -15 "/home/alan/projects/multi-agent-system/$ART/task2_contract_test.txt"
