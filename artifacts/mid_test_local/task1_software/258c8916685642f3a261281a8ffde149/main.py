#!/usr/bin/env python3
"""Meeting Room Booking System.

A small, menu-driven command line application for a team that used to manage
meeting rooms in a shared spreadsheet.  It provides the three workflows the
team asked for:

  1. check availability  - which 30-minute slots are free / taken for a date
  2. book room           - validates and stores a booking (no double booking)
  3. cancel booking      - marks a booking cancelled so the slot frees up

Data is stored in a SQLite database file (bookings.db) so it survives
restarts.  Sample rooms are seeded on first run.

Usage
-----
Interactive menu (recommended for normal users):
    python main.py

Non-interactive shortcuts (handy for scripting / testing):
    python main.py show   --date 2026-10-05 [--room "Boardroom"]
    python main.py book   --date 2026-10-05 --room "Boardroom" \
                          --start 10:00 --end 11:00 \
                          --title "Sprint review" --organiser "Alice"
    python main.py cancel --id 3
    python main.py cancel --room "Boardroom" --date 2026-10-05
    python main.py list   [--date 2026-10-05]

Assumptions (from the requirements spec defaults):
  * Working hours 09:00-18:00, Monday-Friday (Asia/Hong_Kong, UTC+8).
  * Bookings are on 30-minute boundaries; maximum duration 4 hours.
  * A booking is uniquely identified by an integer id.
  * A cancelled booking never blocks a slot (the slot becomes free again).
  * Times are stored and shown in local Hong Kong time.
"""

import os
import sqlite3
import sys
import datetime

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
WORK_START_MIN = 9 * 60          # 09:00
WORK_END_MIN = 18 * 60           # 18:00
SLOT_MINUTES = 30
MAX_BOOKING_MINUTES = 4 * 60     # 4 hours
WORKDAYS = {0, 1, 2, 3, 4}       # Monday..Friday
MAX_LOOKAHEAD_DAYS = 365         # booking horizon

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bookings.db")
DB_PATH = os.environ.get("ROOMBOOK_DB", DEFAULT_DB)

SEED_ROOMS = [
    ("Boardroom", 12),
    ("Meeting Room A", 8),
    ("Huddle Room B", 4),
]


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------
def minutes_to_hhmm(m):
    return "%02d:%02d" % (m // 60, m % 60)


def hhmm_to_minutes(text):
    """Parse 'HH:MM' (also accepts 'H:MM' or 'HHMM'). Raise ValueError."""
    if text is None:
        raise ValueError("Time is required (format HH:MM, e.g. 09:30).")
    text = str(text).strip()
    if ":" not in text and len(text) == 4 and text.isdigit():
        text = text[:2] + ":" + text[2:]
    if ":" not in text:
        raise ValueError("Invalid time %r - use HH:MM, e.g. 09:30." % (text,))
    parts = text.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError("Invalid time %r - use HH:MM, e.g. 09:30." % (text,))
    h, mm = int(parts[0]), int(parts[1])
    if h > 23 or mm > 59:
        raise ValueError("Invalid time %r - hours 00-23, minutes 00-59." % (text,))
    return h * 60 + mm


def all_slots():
    """Return the list of bookable 30-minute slots as (start_min, end_min)."""
    slots = []
    m = WORK_START_MIN
    while m + SLOT_MINUTES <= WORK_END_MIN:
        slots.append((m, m + SLOT_MINUTES))
        m += SLOT_MINUTES
    return slots


def parse_date(date_str, allow_past=False):
    """Validate a date string, returning a datetime.date.  Raise ValueError."""
    if not date_str:
        raise ValueError("Date is required (format YYYY-MM-DD).")
    try:
        d = datetime.date.fromisoformat(str(date_str).strip())
    except ValueError:
        raise ValueError("Invalid date %r - use YYYY-MM-DD, e.g. 2026-10-05." % (date_str,))
    today = datetime.date.today()
    if not allow_past and d < today:
        raise ValueError("Date %s is in the past (today is %s)." % (d.isoformat(), today.isoformat()))
    if d.weekday() not in WORKDAYS:
        raise ValueError("Date %s is a %s - rooms may only be booked Monday to Friday."
                         % (d.isoformat(), d.strftime("%A")))
    if (d - today).days > MAX_LOOKAHEAD_DAYS:
        raise ValueError("Date %s is more than %d days in the future."
                         % (d.isoformat(), MAX_LOOKAHEAD_DAYS))
    return d


def validate_slot(start_min, end_min):
    """Validate booking start/end times.  Raise ValueError with a clear message."""
    if end_min == start_min:
        raise ValueError("End time must be different from start time.")
    if end_min < start_min:
        raise ValueError("End time (%s) must be after start time (%s)."
                         % (minutes_to_hhmm(end_min), minutes_to_hhmm(start_min)))
    if start_min < WORK_START_MIN or end_min > WORK_END_MIN:
        raise ValueError("Bookings must be within working hours %s-%s."
                         % (minutes_to_hhmm(WORK_START_MIN), minutes_to_hhmm(WORK_END_MIN)))
    for label, t in (("Start", start_min), ("End", end_min)):
        if (t - WORK_START_MIN) % SLOT_MINUTES != 0:
            raise ValueError("%s time %s must be on a %d-minute boundary (e.g. 09:00, 09:30)."
                             % (label, minutes_to_hhmm(t), SLOT_MINUTES))
    if end_min - start_min > MAX_BOOKING_MINUTES:
        raise ValueError("Booking too long (%d min) - the maximum is %d minutes (4 hours)."
                         % (end_min - start_min, MAX_BOOKING_MINUTES))


def overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
def get_connection(db_path=None):
    path = db_path or DB_PATH
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rooms (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT    NOT NULL UNIQUE,
            capacity INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bookings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id     INTEGER NOT NULL REFERENCES rooms(id),
            date        TEXT    NOT NULL,          -- YYYY-MM-DD
            start_min   INTEGER NOT NULL,          -- minutes from midnight
            end_min     INTEGER NOT NULL,
            title       TEXT,
            organiser   TEXT,
            status      TEXT    NOT NULL DEFAULT 'confirmed',
            created_at  TEXT    NOT NULL,
            cancelled_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_bookings_room_date
            ON bookings(room_id, date, status);
        """
    )
    if conn.execute("SELECT COUNT(*) AS n FROM rooms").fetchone()["n"] == 0:
        conn.executemany("INSERT INTO rooms(name, capacity) VALUES (?, ?)", SEED_ROOMS)
    conn.commit()


def find_room(conn, room_name):
    """Find a room by name (case-insensitive).  Raise ValueError if unknown."""
    if not room_name or not str(room_name).strip():
        raise ValueError("Room name is required.")
    row = conn.execute(
        "SELECT * FROM rooms WHERE lower(name) = lower(?)", (str(room_name).strip(),)
    ).fetchone()
    if row is None:
        names = ", ".join(r["name"] for r in conn.execute("SELECT name FROM rooms ORDER BY name"))
        raise ValueError("Unknown room %r. Available rooms: %s." % (room_name, names))
    return row


def confirmed_bookings(conn, room_id, date_str):
    return conn.execute(
        "SELECT * FROM bookings WHERE room_id=? AND date=? AND status='confirmed' "
        "ORDER BY start_min",
        (room_id, date_str),
    ).fetchall()


# --------------------------------------------------------------------------
# Workflow 1: check availability
# --------------------------------------------------------------------------
def check_availability(conn, date_str, room_name=None):
    """Return {room_name: [(start, end, is_taken, detail), ...]}.

    A cancelled booking is never reported as taken.
    """
    d = parse_date(date_str)
    date_iso = d.isoformat()
    if room_name:
        rooms = [find_room(conn, room_name)]
    else:
        rooms = conn.execute("SELECT * FROM rooms ORDER BY capacity DESC, name").fetchall()

    result = {}
    for room in rooms:
        booked = confirmed_bookings(conn, room["id"], date_iso)
        slots = []
        for s, e in all_slots():
            taken = None
            for b in booked:
                if overlaps(s, e, b["start_min"], b["end_min"]):
                    taken = b
                    break
            detail = ""
            if taken is not None:
                who = taken["title"] or "booked"
                if taken["organiser"]:
                    who += " (%s)" % taken["organiser"]
                detail = "booking #%d: %s" % (taken["id"], who)
            slots.append((minutes_to_hhmm(s), minutes_to_hhmm(e), taken is not None, detail))
        result[room["name"]] = slots
    return result


def print_availability(conn, date_str, room_name=None):
    try:
        avail = check_availability(conn, date_str, room_name)
    except ValueError as exc:
        print("ERROR: %s" % exc)
        return False
    print("\nAvailability for %s:" % date_str)
    for room, slots in avail.items():
        free = sum(1 for s in slots if not s[2])
        print("\n  %s  (%d of %d slots free)" % (room, free, len(slots)))
        for start, end, taken, detail in slots:
            if taken:
                print("    [X] %s-%s  TAKEN  - %s" % (start, end, detail))
            else:
                print("    [ ] %s-%s  free" % (start, end))
    return True


# --------------------------------------------------------------------------
# Workflow 2: book room
# --------------------------------------------------------------------------
def book_room(conn, date_str, room_name, start, end, title="", organiser=""):
    """Validate and create a booking.  Raise ValueError on any problem."""
    d = parse_date(date_str)
    date_iso = d.isoformat()
    room = find_room(conn, room_name)
    start_min = hhmm_to_minutes(start)
    end_min = hhmm_to_minutes(end)
    validate_slot(start_min, end_min)

    for b in confirmed_bookings(conn, room["id"], date_iso):
        if overlaps(start_min, end_min, b["start_min"], b["end_min"]):
            raise ValueError(
                "Overlap: %s is already booked %s-%s on %s by %s (booking #%d). "
                "Choose another slot or cancel that booking first."
                % (room["name"], minutes_to_hhmm(b["start_min"]), minutes_to_hhmm(b["end_min"]),
                   date_iso, b["organiser"] or b["title"] or "someone", b["id"]))

    cur = conn.execute(
        "INSERT INTO bookings(room_id, date, start_min, end_min, title, organiser, "
        "status, created_at) VALUES (?,?,?,?,?,?,'confirmed',?)",
        (room["id"], date_iso, start_min, end_min, title, organiser,
         datetime.datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    booking_id = cur.lastrowid
    print("SUCCESS: booking #%d confirmed - %s %s on %s, %s-%s%s"
          % (booking_id, room["name"], title and ("(%s)" % title) or "",
             date_iso, minutes_to_hhmm(start_min), minutes_to_hhmm(end_min),
             organiser and (" for %s" % organiser) or ""))
    return booking_id


# --------------------------------------------------------------------------
# Workflow 3: cancel booking
# --------------------------------------------------------------------------
def cancel_booking(conn, booking_id=None, room_name=None, date_str=None, reason=""):
    """Cancel a confirmed booking by id, or by room+date.  Raise ValueError."""
    if booking_id is not None:
        row = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if row is None:
            raise ValueError("No booking with id %s." % booking_id)
        rows = [row]
    elif room_name and date_str:
        d = parse_date(date_str)
        room = find_room(conn, room_name)
        rows = confirmed_bookings(conn, room["id"], d.isoformat())
        if not rows:
            raise ValueError("No confirmed booking for %s on %s."
                             % (room["name"], d.isoformat()))
        if len(rows) > 1:
            lines = "; ".join(
                "#%d %s-%s" % (r["id"], minutes_to_hhmm(r["start_min"]), minutes_to_hhmm(r["end_min"]))
                for r in rows)
            raise ValueError("Multiple confirmed bookings for %s on %s: %s. "
                             "Please cancel a specific one with: cancel --id <id>."
                             % (room["name"], d.isoformat(), lines))
    else:
        raise ValueError("Provide either a booking id, or both room and date.")

    row = rows[0]
    if row["status"] == "cancelled":
        raise ValueError("Booking #%d is already cancelled." % row["id"])
    conn.execute("UPDATE bookings SET status='cancelled', cancelled_at=? WHERE id=?",
                 (datetime.datetime.now().isoformat(timespec="seconds"), row["id"]))
    conn.commit()
    room = conn.execute("SELECT name FROM rooms WHERE id=?", (row["room_id"],)).fetchone()["name"]
    print("SUCCESS: booking #%d cancelled - %s on %s, %s-%s. The slot is now free.%s"
          % (row["id"], room, row["date"],
             minutes_to_hhmm(row["start_min"]), minutes_to_hhmm(row["end_min"]),
             reason and (" Reason: %s" % reason) or ""))
    return row["id"]


def list_bookings(conn, date_str=None, include_cancelled=True):
    sql = ("SELECT b.*, r.name AS room_name FROM bookings b JOIN rooms r ON r.id=b.room_id")
    params = []
    if date_str:
        d = parse_date(date_str, allow_past=True)
        sql += " WHERE b.date=?"
        params.append(d.isoformat())
    sql += " ORDER BY b.date, r.name, b.start_min"
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("No bookings found.")
        return
    print("\n%-4s %-10s %-14s %-12s %-9s %s" % ("ID", "Date", "Room", "Time", "Status", "Purpose"))
    for r in rows:
        if r["status"] == "cancelled" and not include_cancelled:
            continue
        print("%-4d %-10s %-14s %-12s %-9s %s"
              % (r["id"], r["date"], r["room_name"],
                 "%s-%s" % (minutes_to_hhmm(r["start_min"]), minutes_to_hhmm(r["end_min"])),
                 r["status"],
                 " ".join(x for x in [r["title"], r["organiser"]] if x)))


# --------------------------------------------------------------------------
# Interactive menu
# --------------------------------------------------------------------------
def ask(prompt, default=""):
    val = input(prompt).strip()
    return val or default


def menu(conn):
    while True:
        print("\n===== Meeting Room Booking =====")
        print("1) Check availability")
        print("2) Book a room")
        print("3) Cancel a booking")
        print("4) List bookings")
        print("5) Quit")
        choice = input("Choose 1-5: ").strip()
        try:
            if choice == "1":
                date = ask("Date (YYYY-MM-DD): ")
                room = ask("Room (blank = all rooms): ")
                print_availability(conn, date, room or None)
            elif choice == "2":
                date = ask("Date (YYYY-MM-DD): ")
                room = ask("Room: ")
                start = ask("Start time (HH:MM): ")
                end = ask("End time (HH:MM): ")
                title = ask("Purpose/title (optional): ")
                organiser = ask("Your name (optional): ")
                book_room(conn, date, room, start, end, title, organiser)
            elif choice == "3":
                bid = ask("Booking id (blank to use room+date): ")
                if bid:
                    cancel_booking(conn, booking_id=int(bid))
                else:
                    room = ask("Room: ")
                    date = ask("Date (YYYY-MM-DD): ")
                    cancel_booking(conn, room_name=room, date_str=date)
            elif choice == "4":
                date = ask("Date (blank = all): ")
                list_bookings(conn, date or None)
            elif choice == "5":
                print("Goodbye.")
                return
            else:
                print("Please choose 1-5.")
        except ValueError as exc:
            print("ERROR: %s" % exc)
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            return


def parse_args(argv):
    if not argv:
        return None
    cmd = argv[0]
    opts = {}
    i = 1
    while i < len(argv):
        if argv[i].startswith("--"):
            key = argv[i][2:].replace("-", "_")
            val = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else ""
            opts[key] = val
            i += 2 if val != "" else 1
        else:
            i += 1
    return cmd, opts


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    conn = get_connection()
    if not argv:
        menu(conn)
        conn.close()
        return 0
    parsed = parse_args(argv)
    if parsed is None:
        menu(conn)
        conn.close()
        return 0
    cmd, o = parsed
    try:
        if cmd == "show":
            ok = print_availability(conn, o.get("date"), o.get("room") or None)
            return 0 if ok else 1
        if cmd == "book":
            book_room(conn, o.get("date"), o.get("room"), o.get("start"),
                      o.get("end"), o.get("title", ""), o.get("organiser", ""))
            return 0
        if cmd == "cancel":
            if o.get("id"):
                cancel_booking(conn, booking_id=int(o["id"]))
            else:
                cancel_booking(conn, room_name=o.get("room"), date_str=o.get("date"))
            return 0
        if cmd == "list":
            list_bookings(conn, o.get("date") or None)
            return 0
        print("Unknown command %r. Use: show | book | cancel | list (or run with no args)." % cmd)
        return 2
    except ValueError as exc:
        print("ERROR: %s" % exc)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
