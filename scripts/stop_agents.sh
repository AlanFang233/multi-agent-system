#!/usr/bin/env bash
set -euo pipefail

tmux kill-session -t multi-agent-system 2>/dev/null \
  && echo "Agent system stopped." \
  || echo "Agent system is not running."