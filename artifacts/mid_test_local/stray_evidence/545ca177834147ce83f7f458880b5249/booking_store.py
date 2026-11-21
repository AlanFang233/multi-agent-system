#!/usr/bin/env python3
"""Booking system core: SQLite schema, seed data and booking primitives.

This is the single source of truth for the booking *records* that the
dashboard reads. Schema matches the QA oracle (validate_metrics.py):
  rooms(id, name, capacity)
  bookings(id, room_id, date, start_min, end_min, title, organiser,
           status, created_at, cancelled_at)
"""
import os
import sqlite3
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "bookings.db")

SLOT = 30
OPEN_MIN = 9 * 60      # 09:00
CLOSE_MIN = 18 * 60    # 18:00
MAX_BOOKING_MIN = 4 * 60

ROOMS = [(1, "Boardroom", 12), (2, "Meeting Room A", 8), (3, "Huddle Room B", 4)]

# (id, room_id, date, start_min, end_min, title, organiser, status)
SEED_BOOKINGS = [
    (1, 1, "2026-11-23", 540, 660, "Sprint review", "Alice", "confirmed"),
    (2, 1, "2026-11-23", 840, 960, "Board meeting", "Dana", "confirmed"),
    (3, 2, "2026-11-23", 600, 660, "1:1", "Ben", "confirmed"),
    (4, 1, "2026-11-24", 600, 720, "Design workshop", "Cara", "confirmed"),
    (5, 2, "2026-11-24", 540, 600, "Standup", "Ben", "confirmed"),
    (6, 2, "2026-11-24", 900, 990, "Client call", "Alice", "confirmed"),
    (7, 3, "2026-11-24", 660, 720, "Huddle", "Ben", "confirmed"),
    (8, 1, "2026-11-24", 960, 1020, "Retro", "Dana", "cancelled"),
    (9, 1, "2026-11-25", 570, 660, "Planning", "Cara", "confirmed"),
    (10, 2, "2026-11-25", 840, 900, "Interview", "Dana", "confirmed"),
    (11, 2, "2026-11-25", 540, 600, "Standup", "Ben", "cancelled"),
    (12, 1, "2026-11-26", 780, 900, "Architecture review", "Alice", "confirmed"),
    (13, 2, "2026-11-26", 600, 720, "Training", "Cara", "confirmed"),
    (14, 3, "2026-11-26", 960, 1020, "Huddle", "Ben", "confirmed"),
    (15, 1, "2026-11-27", 600, 690, "Sprint review", "Alice", "confirmed"),
    (16, 2, "2026-11-27", 540, 600, "Standup", "Dana", "confirmed"),
    (17, 3, "2026-11-27", 840, 900, "Huddle", "Cara", "cancelled"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    capacity INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    start_min INTEGER NOT NULL,
    end_min INTEGER NOT NULL,
    title TEXT NOT NULL,
    organiser TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed',
    created_at TEXT,
    cancelled_at TEXT
);
"""


def connect(path=DEFAULT_DB, read_only=False):
    if path == ":memory:":
        conn = sqlite3.connect(":memory:")
    elif read_only:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path=DEFAULT_DB):
    """Create schema and seed records if the DB is empty/absent."""
    conn = connect(path)
    conn.executescript(SCHEMA)
    n = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    if n == 0:
        conn.executemany("INSERT INTO rooms (id,name,capacity) VALUES (?,?,?)", ROOMS)
        now = datetime.datetime.now().isoformat(timespec="seconds")
        for b in SEED_BOOKINGS:
            bid, rid, d, s, e, title, org, status = b
            conn.execute(
                "INSERT INTO bookings (id,room_id,date,start_min,end_min,title,organiser,"
                "status,created_at,cancelled_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (bid, rid, d, s, e, title, org, status, now,
                 now if status == "cancelled" else None),
            )
        conn.commit()
    return conn


# ---- booking primitives (the "existing booking system" API) ----
def all_slots():
    return list(range(OPEN_MIN, CLOSE_MIN, SLOT))


def overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def confirmed_bookings(conn, date=None, room_id=None):
    q = "SELECT * FROM bookings WHERE status='confirmed'"
    args = []
    if date:
        q += " AND date=?"
        args.append(date)
    if room_id:
        q += " AND room_id=?"
        args.append(room_id)
    return conn.execute(q, args).fetchall()


def list_bookings(conn, date=None):
    if date:
        return conn.execute(
            "SELECT b.*, r.name room FROM bookings b JOIN rooms r ON r.id=b.room_id "
            "WHERE b.date=? ORDER BY b.start_min", (date,)).fetchall()
    return conn.execute(
        "SELECT b.*, r.name room FROM bookings b JOIN rooms r ON r.id=b.room_id "
        "ORDER BY b.date, r.name, b.start_min").fetchall()


def book_room(conn, date, room, start_min, end_min, title, organiser):
    rid = conn.execute("SELECT id FROM rooms WHERE name=?", (room,)).fetchone()
    if not rid:
        return False, "unknown room %s" % room
    rid = rid["id"]
    for b in confirmed_bookings(conn, date, rid):
        if overlaps(start_min, end_min, b["start_min"], b["end_min"]):
            return False, "overlaps booking #%d" % b["id"]
    cur = conn.execute(
        "INSERT INTO bookings (room_id,date,start_min,end_min,title,organiser,status,created_at)"
        " VALUES (?,?,?,?,?,?,'confirmed',datetime('now'))",
        (rid, date, start_min, end_min, title, organiser))
    conn.commit()
    return True, cur.lastrowid


def cancel_booking(conn, booking_id):
    n = conn.execute(
        "UPDATE bookings SET status='cancelled', cancelled_at=datetime('now') "
        "WHERE id=? AND status='confirmed'", (booking_id,)).rowcount
    conn.commit()
    return n > 0
