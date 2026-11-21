#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="multi-agent-system"
PYTHON="$ROOT/.venv/bin/python"

if ! command -v tmux >/dev/null 2>&1; then
    echo "[ERROR] tmux is not installed"
    exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
    echo "[ERROR] Python virtualenv not found: $PYTHON"
    exit 1
fi

start_a2a() {
    local role="$1"
    local port="$2"
    local window="a2a-$role"
    local command

    # Avoid starting another server on an occupied port.
    if ss -H -ltn "( sport = :$port )" 2>/dev/null | grep -q .; then
        echo "[SKIP] Port $port already in use ($role)"
        return 0
    fi

    # Avoid duplicate tmux windows.
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        if tmux list-windows -t "$SESSION" -F '#{window_name}' \
            | grep -Fxq "$window"; then
            echo "[SKIP] Window already exists: $window"
            return 0
        fi
    fi

    printf -v command \
        'cd %q && exec %q -m agent_ecosystem.a2a_server --name %q --port %q' \
        "$ROOT" "$PYTHON" "$role" "$port"

    if tmux has-session -t "$SESSION" 2>/dev/null; then
        tmux new-window \
            -d \
            -t "$SESSION:" \
            -n "$window" \
            "$command"
    else
        tmux new-session \
            -d \
            -s "$SESSION" \
            -n "$window" \
            "$command"
    fi

    echo "[START] $role A2A server on port $port"
}

start_a2a research 8001
start_a2a coding 8002
start_a2a analysis 8003

echo
echo "[INFO] A2A startup commands submitted."
echo "[INFO] tmux session: $SESSION"
echo "[INFO] Check server logs and listening ports."
