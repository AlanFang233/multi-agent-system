#!/usr/bin/env bash
cd ~/projects/multi-agent-system || exit 1
echo "=== processes ==="
ps -eo pid,args | grep -aE 'a2a_server|mock_llm_server|mcp_calculator|coordinator' | grep -v grep
echo "=== coordinator health ==="
curl -s --max-time 10 http://127.0.0.1:8000/health 2>/dev/null || echo 'NOT_RESPONDING'
echo "=== coordinator log (last 15) ==="
tail -15 logs/coordinator.log 2>/dev/null || echo 'NO_LOG_FILE'