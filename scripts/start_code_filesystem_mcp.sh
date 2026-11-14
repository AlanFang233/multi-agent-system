#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"
nvm use 24 >/dev/null

exec npx -y \
  @modelcontextprotocol/server-filesystem@2.3.0 \
  --confine-roots \
  "$ROOT/src" \
  "$ROOT/tests" \
  "$ROOT/mcp_workspace"