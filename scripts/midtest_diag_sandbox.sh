#!/usr/bin/env bash
# Reproduce the agent sandbox pytest crash with the EXACT docker invocation
# from sandbox_mcp_server.py, against the dev agent's live sandbox.
cd ~/projects/multi-agent-system || exit 2
WS="$PWD/mcp_workspace/sandboxes/e0ffc0fea8474c4293c3ed1de0b0e724"
echo "=== sandbox contents ==="
ls "$WS" | head -25
echo "=== pytest repro ==="
timeout 90 docker run --rm --pull=never --name diag_pytest \
  --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges=true --pids-limit 64 \
  --memory 512m --cpus 1 --user 65534:65534 \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
  --mount "type=bind,src=$WS,dst=/work,readonly" \
  --workdir /work -e HOME=/tmp \
  -e PYTHONDONTWRITEBYTECODE=1 -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  agent-sandbox-py:3.12-v1 \
  python -B -m pytest -q --maxfail=1 --tb=short -p no:cacheprovider \
  --basetemp=/tmp/pytest test_dashboard_metrics.py 2>&1 | head -30
echo "=== pytest import check ==="
timeout 60 docker run --rm --pull=never --name diag_pyimp \
  --network none --read-only --cap-drop ALL --user 65534:65534 \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
  --mount "type=bind,src=$WS,dst=/work,readonly" \
  --workdir /work -e HOME=/tmp -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  agent-sandbox-py:3.12-v1 \
  python -B -c "import pytest, sys; print('pytest', pytest.__version__, 'py', sys.version.split()[0]); import tempfile; d=tempfile.mkdtemp(); print('tmpdir ok', d)" 2>&1 | head -10
