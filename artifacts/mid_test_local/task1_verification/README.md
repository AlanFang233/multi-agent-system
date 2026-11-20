# Meeting Room Booking System

A small command-line app that replaces the shared spreadsheet. It prevents
double bookings and always reflects cancellations.

## Requirements
Python 3.8+ (standard library only — no install needed).

## Start the app

    cd <this folder>
    python3 main.py

You get a numbered menu:

    1) Check availability
    2) Book a room
    3) Cancel a booking
    4) List bookings
    5) Quit

Just follow the prompts. Nothing to program.

### One-shot commands (optional, for scripting/testing)

    python3 main.py show   --date 2026-10-05 [--room "Boardroom"]
    python3 main.py book   --date 2026-10-05 --room "Boardroom" \
                           --start 10:00 --end 11:00 \
                           --title "Sprint review" --organiser "Alice"
    python3 main.py cancel --id 1
    python3 main.py cancel --room "Boardroom" --date 2026-10-05
    python3 main.py list   [--date 2026-10-05]

## Data
Stored in `bookings.db` (SQLite) next to the script, so bookings survive
restarts. Delete the file to reset (sample rooms are re-seeded on start).
Override the location with the `ROOMBOOK_DB` environment variable.

Seeded rooms: Boardroom (12), Meeting Room A (8), Huddle Room B (4).

## Rules
- Working hours 09:00–18:00, Monday–Friday (Asia/Hong_Kong, UTC+8).
- 30-minute slots; a booking must start/end on a :00 or :30 boundary.
- Maximum booking length 4 hours; no past dates; up to 365 days ahead.
- **No overlap:** a booking is rejected if it overlaps a *confirmed* booking
  for the same room (back-to-back bookings are fine).
- **Cancel frees the slot:** a cancelled booking is kept for the record but
  never counts as taken, so the slot becomes instantly bookable again.

## Files
- `main.py`   — the application (storage + three workflows + menu).
- `demo.py`   — end-to-end scripted run of all three workflows + error cases.
- `checks.py` — restart-persistence and menu smoke test.
