#!/usr/bin/env bash
set -e
cd ~/projects/multi-agent-system
echo "restarting coordinator..."
pkill -f "agent_ecosystem.coordinator" 2>/dev/null || true
sleep 2
nohup ./.venv/bin/python -m agent_ecosystem.coordinator --port 8000 > logs/coordinator.log 2>&1 &
sleep 5
printf "coordinator: "
curl -s --max-time 10 http://127.0.0.1:8000/health
echo
echo "submitting smoke test..."
WF=$(curl -s -X POST http://127.0.0.1:8000/workflows -H "Content-Type: application/json" -d "{\"goal\":\"Research the topic of RAG and write a summary.\"}")
echo "$WF"
WF_ID=$(echo "$WF" | python3 -c 'import json,sys; print(json.load(sys.stdin)["workflow_id"])')
for i in $(seq 1 60); do
  STATUS=$(curl -s http://127.0.0.1:8000/workflows/"$WF_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["workflow"]["status"])')
  echo "[$i] status=$STATUS"
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ]; then
    curl -s http://127.0.0.1:8000/workflows/"$WF_ID" | python3 -c 'import json,sys; d=json.load(sys.stdin); w=d["workflow"]; print("ERROR:", w.get("error","")[:300])'
    break
  fi
  sleep 3
done
echo "SMOKE_DONE"