
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="multi-agent-system"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Agent system is already running in tmux."
    tmux list-windows -t "$SESSION"
    exit 0
fi

echo "Starting Coordinator only..."

tmux new-session -d -s "$SESSION" -n coordinator \
    "cd '$ROOT' && source .venv/bin/activate && exec env MAS_COORDINATOR_ONLY=1 python -m agent_ecosystem.coordinator --host 127.0.0.1 --port 8000"

echo "Coordinator launched; worker agents are created on demand."
tmux list-windows -t "$SESSION"
echo "Dashboard: http://127.0.0.1:8000/dashboard"
