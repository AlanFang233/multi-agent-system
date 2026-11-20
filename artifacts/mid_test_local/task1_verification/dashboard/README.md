# Room Utilisation Dashboard (front end)

Read-only analytics UI over the booking store. No backend changes were required:
the UI already speaks the exact `bookings`/`rooms` field names and status
semantics published in `DATA_CONTRACT.md`.

## Files
- `index.html` — the dashboard page (filters, KPI cards, charts, summary, states).
- `dashboard.js` — aggregation + SVG chart renderers + mock/live data modes.
- `DATA_CONTRACT.md` — frozen field names, status semantics, metric definitions.
- `test_contract.py` — contract test; runs against the real `bookings.db` and
  checks the JS metric rules (as their Python twin) against an oracle.

## Run it
Open `index.html` in a browser. It starts in **mock mode** (deterministic data,
no backend), so you can review and test before wiring the backend.

To use real data, serve the booking store over JSON and set:
```js
BookingDashboard.CONFIG.mode = "live";
BookingDashboard.CONFIG.dataUrl = "/api/dashboard";
```
No other change is needed — `fetchData()` accepts either the raw
`{rooms, bookings}` store or the pre-aggregated payload in `DATA_CONTRACT.md` §4.

## What the manager sees
- Date-range picker with presets **This week / This month / All data / Custom**,
  plus an optional **room filter** (defaults to all rooms; range defaults to the
  full data span).
- KPI cards: confirmed bookings, reserved hours, cancellations, cancellation
  rate, workdays in range.
- Charts: bookings per room (bar), reserved time per room (bar, hours),
  bookings per day (bar, auto-switches to a line when >14 days), weekday × hour
  busy-times heatmap, bookings by weekday (bar), and cancellations
  (confirmed vs cancelled bar + per-room rate table).
- Summary panel: all rooms ordered by utilisation with % bars and
  over/under-utilisation flags (under <25%, ok 25–80%, over >80%).
- States: loading spinner, explicit empty-range message, and an error banner if
  a live fetch fails. Responsive grid (auto-fit) for desktop/tablet/mobile.

## Test
```
python dashboard/test_contract.py
```
Verifies: confirmed-only occupancy, inclusive date range, reserved-minute sum,
cancel rate, blank-range handling, per-slot heatmap values, and exact payload
field names for both live and mock paths.

> Scope note (v1): only `confirmed` and `cancelled` exist in the data, so
> no-show / rejected / expired are excluded — they need new data capture.
