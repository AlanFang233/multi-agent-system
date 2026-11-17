#!/usr/bin/env bash
# Submit a simple workflow and verify it completes with a real result.
set -e
cd ~/projects/multi-agent-system
WF=$(curl -s -X POST http://127.0.0.1:8000/workflows -H "Content-Type: application/json" \
  -d '{"goal":"Research the three key components of a RAG system and summarize them."}')
echo "SUBMIT: $WF"
WF_ID=$(echo "$WF" | python3 -c 'import json,sys; print(json.load(sys.stdin)["workflow_id"])')
echo "WF_ID=$WF_ID"

for i in $(seq 1 60); do
  RAW=$(curl -s "http://127.0.0.1:8000/workflows/$WF_ID")
  STATUS=$(echo "$RAW" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["workflow"]["status"])')
  NODES=$(echo "$RAW" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("nodes",[])))')
  echo "[$i] status=$STATUS nodes=$NODES"
  if [ "$STATUS" = "completed" ]; then
    echo "=== DAG ==="
    echo "$RAW" | python3 -c '
import json,sys
d=json.load(sys.stdin)
for n in d["nodes"]:
  print(f"  {n[\"plan_key\"]:20s} {n[\"status\"]:12s} agent={n.get(\"assigned_agent\",\"?\")}")
'
    echo "=== RESULT ==="
    curl -s "http://127.0.0.1:8000/workflows/$WF_ID/result" | python3 -c '
import json,sys; d=json.load(sys.stdin)
print(d.get("final_result","NO RESULT")[:1500])
'
    echo "VERDICT: PASS"
    exit 0
  fi
  if [ "$STATUS" = "failed" ]; then
    echo "ERROR: $(echo "$RAW" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["workflow"].get("error","unknown")[:300])')"
    echo "VERDICT: FAIL"
    exit 1
  fi
  sleep 3
done
echo "TIMEOUT"
exit 1