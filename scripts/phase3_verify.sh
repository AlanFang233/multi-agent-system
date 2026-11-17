#!/usr/bin/env bash
cd ~/projects/multi-agent-system
echo "=== restarting ==="
bash scripts/phase3_start.sh > /dev/null 2>&1
echo "DONE"
echo "=== endpoints ==="
for ep in health dashboard workflows approvals clarifications; do
  printf '  /%s: ' "$ep"
  curl -s --max-time 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:8000/${ep}"
  echo
done
echo "=== pytest ==="
./.venv/bin/python -m pytest -q 2>&1 | tail -3