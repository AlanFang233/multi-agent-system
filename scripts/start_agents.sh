
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="multi-agent-system"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Agent system is already running in tmux."
    tmux list-windows -t "$SESSION"
    exit 0
fi

echo "Starting Multi-Agent System..."

tmux new-session -d -s "$SESSION" -n research \
    "cd '$ROOT' && source .venv/bin/activate && exec python -m agent_ecosystem.a2a_server --name research --port 8001"

tmux new-window -t "$SESSION" -n coding \
    "cd '$ROOT' && source .venv/bin/activate && exec python -m agent_ecosystem.a2a_server --name coding --port 8002"

tmux new-window -t "$SESSION" -n analysis \
    "cd '$ROOT' && source .venv/bin/activate && exec python -m agent_ecosystem.a2a_server --name analysis --port 8003"

echo "Agent processes launched."
tmux list-windows -t "$SESSION"
echo "Research: http://127.0.0.1:8001"
echo "Coding:   http://127.0.0.1:8002"
echo "Analysis: http://127.0.0.1:8003"