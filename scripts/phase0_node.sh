#!/usr/bin/env bash
# Phase 0: ensure nvm + Node 24 (required by scripts/start_*filesystem_mcp.sh).
set -e
export NVM_DIR="$HOME/.nvm"
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  echo "installing nvm..."
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.41.0/install.sh | bash >/dev/null
fi
. "$NVM_DIR/nvm.sh"
if ! nvm ls 24 >/dev/null 2>&1; then
  echo "installing node 24..."
  nvm install 24
fi
nvm alias default 24 >/dev/null
nvm use 24 >/dev/null
echo "node=$(node --version) npm=$(npm --version) nvm=$(nvm --version)"
echo "NODE_READY"
