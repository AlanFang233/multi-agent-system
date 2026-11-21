#!/usr/bin/env bash
# Launch the acceptance run detached; survives the parent wsl.exe exiting.
cd ~/projects/multi-agent-system || exit 2
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
# Guard: never run two acceptance instances in parallel (they interleave
# workflows and corrupt each other's evidence).
if pgrep -f "acceptance_e2e.py" > /dev/null 2>&1; then
  echo "ALREADY_RUNNING:"
  pgrep -af "acceptance_e2e.py"
  exit 1
fi
nohup ./.venv/bin/python -u scripts/acceptance_e2e.py > logs/acceptance.log 2>&1 &
PID=$!
disown "$PID" 2>/dev/null || true
sleep 1
if kill -0 "$PID" 2>/dev/null; then
  echo "ACCEPTANCE_RUNNING pid=$PID"
else
  echo "ACCEPTANCE_DIED pid=$PID"
  tail -20 logs/acceptance.log
fi
