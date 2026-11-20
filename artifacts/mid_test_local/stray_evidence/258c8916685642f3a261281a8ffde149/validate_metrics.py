#!/usr/bin/env python3
"""Independent re-derivation of analytics metrics over bookings.db.

Computes, directly from the raw booking records (no project code reused):
  - per-room booking counts (confirmed)
  - total reserved minutes/clock time (confirmed)
  - busiest day(s) and busiest 30-min time slots
  - cancellation rate
Also dumps schema, row counts, status values, date range and a few edge probes.
"""
import os
import sqlite3
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "bookings.db")
conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
conn.row_factory = sqlite3.Row

print("== SCHEMA ==")
for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index')"):
    print("-", r["name"])

print("\n== ROOMS ==")
for r in conn.execute("SELECT * FROM rooms"):
    print(dict(r))

print("\n== BOOKINGS TOTAL ==")
print("total rows:", conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0])

print("\n== STATUS VALUES (defensive: any unknown status?) ==")
for r in conn.execute("SELECT status, COUNT(*) n FROM bookings GROUP BY status"):
    print(dict(r))

print("\n== DATE RANGE ==")
row = conn.execute("SELECT MIN(date) mn, MAX(date) mx, COUNT(DISTINCT date) nd FROM bookings").fetchone()
print(dict(row))

print("\n== PER-ROOM (all statuses) ==")
for r in conn.execute("""SELECT r.name, b.status, COUNT(*) n
                         FROM bookings b JOIN rooms r ON r.id=b.room_id
                         GROUP BY r.name, b.status ORDER BY r.name, b.status"""):
    print(dict(r))

print("\n== INTEGRITY CHECKS ==")
print("bookings with unknown status:",
      conn.execute("SELECT COUNT(*) FROM bookings WHERE status NOT IN ('confirmed','cancelled')").fetchone()[0])
print("cancelled rows missing cancelled_at:",
      conn.execute("SELECT COUNT(*) FROM bookings WHERE status='cancelled' AND cancelled_at IS NULL").fetchone()[0])
print("orphan room_id:",
      conn.execute("SELECT COUNT(*) FROM bookings WHERE room_id NOT IN (SELECT id FROM rooms)").fetchone()[0])

print("\n== FULL RAW LISTING (id, room, date, start, end, status) ==")
for r in conn.execute("""SELECT b.id, r.name, b.date, b.start_min, b.end_min, b.status
                         FROM bookings b JOIN rooms r ON r.id=b.room_id
                         ORDER BY b.date, r.name, b.start_min"""):
    print("%4d %-14s %s %5d-%5d %s" % (r["id"], r["name"], r["date"], r["start_min"], r["end_min"], r["status"]))


def metrics(lo, hi, statuses=("confirmed",)):
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        "SELECT b.*, r.name room FROM bookings b JOIN rooms r ON r.id=b.room_id "
        "WHERE b.date>=? AND b.date<=? AND b.status IN (%s)" % placeholders,
        (lo, hi) + tuple(statuses)).fetchall()
    per_room = {}
    total_min = 0
    per_day = {}
    per_slot = {}
    for b in rows:
        dur = b["end_min"] - b["start_min"]
        per_room[b["room"]] = per_room.get(b["room"], 0) + 1
        total_min += dur
        per_day[b["date"]] = per_day.get(b["date"], 0) + 1
        s = b["start_min"]
        while s < b["end_min"]:
            per_slot[s] = per_slot.get(s, 0) + 1
            s += 30
    return rows, per_room, total_min, per_day, per_slot


def show(label, lo, hi):
    rows, per_room, total_min, per_day, per_slot = metrics(lo, hi)
    canc = conn.execute("SELECT COUNT(*) FROM bookings WHERE date>=? AND date<=?", (lo, hi)).fetchone()[0]
    alln = conn.execute(
        "SELECT COUNT(*) FROM bookings WHERE date>=? AND date<=?", (lo, hi)).fetchone()[0]
    print("\n=== RANGE %s .. %s [%s] ===" % (lo, hi, label))
    print("confirmed bookings:", len(rows), " total rows:", alln,
          " cancelled:", canc, " cancel_rate=%.3f" % (canc / alln if alln else 0.0))
    print("per-room confirmed counts:", per_room)
    print("total reserved minutes:", total_min, "= %.2f h" % (total_min / 60.0))
    print("per-day counts:", dict(sorted(per_day.items())))
    if per_day:
        mx = max(per_day.values())
        print("busiest day(s):", [d for d, n in sorted(per_day.items()) if n == mx], "n=", mx)
    if per_slot:
        mx = max(per_slot.values())
        busiest = sorted(s for s, n in per_slot.items() if n == mx)
        print("busiest slot(s):", ["%02d:%02d" % (s // 60, s % 60) for s in busiest], "n=", mx)
    print("slot histogram:", {("%02d:%02d" % (s // 60, s % 60)): n for s, n in sorted(per_slot.items())})


# Determine the actual span then test several ranges
span = conn.execute("SELECT MIN(date) mn, MAX(date) mx FROM bookings").fetchone()
print("\n\n########## METRIC RE-DERIVATION ##########")
show("whole span", span["mn"], span["mx"])

# empty range probe (a valid weekday with no bookings after the data)
show("empty-future-range", "2027-03-01", "2027-03-05")
# single-booking-ish range
show("single-day", span["mn"], span["mn"])
# boundary probe: exclusive end would drop last day
show("boundary-inclusive", span["mn"], span["mx"])
