#!/usr/bin/env bash
# Recover artifacts misplaced by the filesystem_code relative-path bug:
# preserve evidence, restore deliverables to their intended workspaces,
# then remove the stray src/sandboxes tree.
set -e
cd ~/projects/multi-agent-system
WS=mcp_workspace/sandboxes
S1=258c8916685642f3a261281a8ffde149
S2=545ca177834147ce83f7f458880b5249
mkdir -p artifacts/mid_test_local/stray_evidence
cp -r src/sandboxes/. artifacts/mid_test_local/stray_evidence/
# Task 1 missing deliverable, written by release agent but mislocated
cp "src/sandboxes/$S1/HOW-TO-USE.md" "$WS/$S1/HOW-TO-USE.md"
# Task 2 integration suite written by dev but mislocated (probes excluded)
for f in booking_store.py dashboard_ui.py main.py metrics_engine.py run_e2e.py test_e2e.py RUN_INSTRUCTIONS.md; do
  cp "src/sandboxes/$S2/$f" "$WS/$S2/$f"
done
rm -rf src/sandboxes
echo RESTORED
ls "$WS/$S1/"
echo ---
ls "$WS/$S2/"
