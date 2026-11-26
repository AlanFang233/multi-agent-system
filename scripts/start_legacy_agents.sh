#!/usr/bin/env bash
# Mount the three legacy base agents (compat mode) onto a running Coordinator.
cd ~/projects/multi-agent-system || exit 2
for entry in research:8001 coding:8002 analysis:8003; do
  name="${entry%:*}"; port="${entry#*:}"
  nohup ./.venv/bin/python -m agent_ecosystem.a2a_server --name "$name" --port "$port" \
    > "logs/$name.log" 2>&1 &
done
sleep 6
curl -s --max-time 3 http://127.0.0.1:8001/health; echo
curl -s --max-time 3 http://127.0.0.1:8002/health; echo
curl -s --max-time 3 http://127.0.0.1:8003/health; echo
