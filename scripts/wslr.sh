#!/usr/bin/env bash
# Gateway for ad-hoc WSL commands from Git Bash (the ZCode Bash tool shell).
#
# Git Bash mangles what wsl.exe receives: `$VAR`/`$(...)` are expanded (or
# stripped) by the Windows shell first, and leading-slash arguments are
# MSYS-path-converted. Both failure classes disappear if the payload travels
# base64-encoded inside a single quoted argument:
#
#   bash scripts/wslr.sh 'cd ~/projects/multi-agent-system && for p in 8001 8002; do curl -s http://127.0.0.1:$p/health; done'
#
# Long-lived or multi-step work should still live in real script files; this
# gateway is for short inspections where writing a file is overkill.
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo "usage: bash scripts/wslr.sh '<wsl bash command string>'" >&2
  exit 2
fi
payload="$(printf '%s' "$1" | base64 -w 0)"
exec wsl.exe -d Ubuntu bash -c "echo '$payload' | base64 -d | bash"
