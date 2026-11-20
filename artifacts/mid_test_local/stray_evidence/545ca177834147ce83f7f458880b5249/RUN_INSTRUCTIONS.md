# Meeting Room Dashboard — Run Instructions

## What was integrated
`metrics_engine` + `dashboard_ui` wired to the booking system's real records
(`booking_store`/`bookings.db`). The dashboard reads the metrics contract
returned by `metrics_engine` and never recomputes them.

## Setup (once)
    cd sandboxes/545ca177834147ce83f7f458880b5249
    python3 main.py init                 # creates bookings.db with seed records

## Run the dashboard (text report)
    python3 dashboard_ui.py --start 2026-11-23 --end 2026-11-27

## Run the dashboard (live service)
    python3 dashboard_ui.py --serve --port 8765
    # then browse http://127.0.0.1:8765/?start=2026-11-23&end=2026-11-27

## Full end-to-end self-test (start service, load dashboard, switch ranges)
    python3 run_e2e.py

## Metrics only (JSON)
    python3 metrics_engine.py --start 2026-11-23 --end 2026-11-27 --json

## Known limitations
- No web framework/matplotlib: charts are ASCII bars (text) and CSS bars (HTML).
  Keeps it dependency-free for the offline sandbox.
- Cancel rate counts cancelled / all rows in range (per the QA oracle definition);
  it is not per-room.
- `main.py` is a minimal reconstruction of the booking CLI (the original was not
  present in the workspace); dashboard only depends on the DB schema, which
  matches the validated model.
