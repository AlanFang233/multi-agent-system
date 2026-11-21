#!/usr/bin/env bash
# Restart ONLY the coordinator in REAL-model mode (HKGAI env from .env.hkgai.bak;
# .env on disk stays Mock and untouched). Group/base agents keep running.
cd ~/projects/multi-agent-system || exit 2
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
while IFS='=' read -r k v; do
  case "$k" in ''|\#*) continue ;; OPENAI_*) ;; *) continue ;; esac
  v="$(printf '%s' "$v" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  export "$k=$v"
done < .env.hkgai.bak
pkill -f 'agent_ecosystem[.]coordinator' 2>/dev/null || true
sleep 2
nohup ./.venv/bin/python -m agent_ecosystem.coordinator --host 0.0.0.0 --port 8000 >> logs/coordinator.log 2>&1 &
sleep 4
curl -s --max-time 5 http://127.0.0.1:8000/health && echo " COORD_RESTARTED_REAL model=${OPENAI_MODEL}"
