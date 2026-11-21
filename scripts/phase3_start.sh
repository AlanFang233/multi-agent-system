#!/usr/bin/env bash
# Offline startup: Mock provider + Coordinator only. Legacy workers are opt-in.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$ROOT/scripts/start_coordinator.sh" --mock "$@"
