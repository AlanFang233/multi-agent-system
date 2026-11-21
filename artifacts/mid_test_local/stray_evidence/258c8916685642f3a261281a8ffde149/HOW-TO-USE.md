# Meeting Room Booking — How to Use It (and What Was Built)

## What it is / what it replaces
A small program that replaces our shared spreadsheet. It removes the two problems we had:
- **Double bookings** — a room+time slot can hold only ONE confirmed booking; any overlap is rejected.
- **Stale records** — cancelling a booking instantly frees its slot, so availability is always current.
No installation needed (Python 3 only). Data survives restarts.

## Start it (exact command)
    cd sandboxes/258c8916685642f3a261281a8ffde149
    python3 main.py
Menu: 1) Check availability  2) Book a room  3) Cancel a booking  4) List bookings  5) Quit
**Data is stored in `bookings.db`** (SQLite) next to `main.py`. Seeded rooms: Boardroom (12), Meeting Room A (8), Huddle Room B (4).

## Rules in effect
- Rooms: Boardroom, Meeting Room A, Huddle Room B.
- Working hours 09:00–18:00, Monday–Friday (Hong Kong time). No past dates; up to 365 days ahead.
- 30-minute slots; times must be on a :00 or :30 boundary. Max booking length 4 hours.
- No-overlap: rejected if it overlaps a *confirmed* booking in the same room. Back-to-back is allowed.
- Who can cancel: anyone can cancel any booking by its id (no login/owner check — see Assumptions).

## Step-by-step walkthrough
1) Check availability
    python3 main.py show --date 2026-10-05
    python3 main.py show --date 2026-10-05 --room "Boardroom"
   Each 30-min slot prints `free` or `[X] ... TAKEN - booking #N: <purpose> (<name>)`.
2) Book a room
    python3 main.py book --date 2026-10-05 --room "Boardroom" --start 10:00 --end 11:00 --title "Sprint review" --organiser "Alice"
   -> `SUCCESS: booking #1 confirmed - Boardroom (Sprint review) on 2026-10-05, 10:00-11:00 for Alice`
3) Cancel a booking
    python3 main.py cancel --id 1
    python3 main.py cancel --room "Boardroom" --date 2026-10-05
   -> `SUCCESS: booking #1 cancelled ... The slot is now free.`
Extras: `python3 main.py list` (all), `list --date 2026-10-05`.
