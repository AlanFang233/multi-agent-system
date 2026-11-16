#!/usr/bin/env bash
# Phase 0 verification helper: processes, health, agent cards.
set -uo pipefail
echo "=== listening processes ==="
ps -eo pid,args | grep -E "a2a_server|mock_llm_server|mcp_calculator" | grep -v grep || echo "(none)"
echo
echo "=== health ==="
for p in 8001 8002 8003; do
  printf 'port %s: ' "$p"
  curl -s --max-time 5 "http://127.0.0.1:${p}/health" || printf 'NO-RESPONSE'
  echo
done
echo
echo "=== agent cards ==="
for p in 8001 8002 8003; do
  printf 'port %s: ' "$p"
  curl -s --max-time 5 "http://127.0.0.1:${p}/.well-known/agent-card.json" \
    | python3 -c 'import json,sys
try:
    c=json.load(sys.stdin)
    print(c["name"],"|",c["version"],"|",[i["url"] for i in c.get("supported_interfaces",[])],"|",[s["id"] for s in c.get("skills",[])])
except Exception as e:
    print("CARD-ERROR",e)'
done
