#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"
nvm use 24 >/dev/null

if [[ -n "${MAS_FILESYSTEM_ROOT:-}" ]]; then
  cd "$MAS_FILESYSTEM_ROOT"
  exec npx -y @modelcontextprotocol/server-filesystem@2.3.0 \
    --confine-roots "$MAS_FILESYSTEM_ROOT"
fi

# Pin the server CWD to the repo root and list mcp_workspace first: with
# --confine-roots, relative paths used to resolve under src/ (first root),
# silently misplacing agent writes (Mid_Test bug #9: sandbox files landed in
# src/sandboxes/ instead of mcp_workspace/sandboxes/). Now "mcp_workspace/..."
# relative paths resolve correctly and bare "sandboxes/..." is denied loudly.
cd "$ROOT"

exec npx -y \
  @modelcontextprotocol/server-filesystem@2.3.0 \
  --confine-roots \
  "$ROOT/mcp_workspace" \
  "$ROOT/src" \
  "$ROOT/tests"
