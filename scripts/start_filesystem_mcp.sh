
#!/usr/bin/env bash
set -euo pipefail

# 加载 Node.js 环境
export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"

# 切换到已安装的 Node.js 24
nvm use 24 >/dev/null

# 启动 Filesystem MCP Server
exec npx -y \
  @modelcontextprotocol/server-filesystem@2.3.0 \
  --read-only \
  "$HOME/projects/multi-agent-system/mcp_workspace"
