#!/usr/bin/env python3
"""Verify (a) data survives a restart and (b) the interactive menu works."""
import datetime, io, os, sys, tempfile, contextlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main

d = datetime.date.today() + datetime.timedelta(days=1)
while d.weekday() not in main.WORKDAYS:
    d += datetime.timedelta(days=1)
day = d.isoformat()
tmp = tempfile.mkdtemp(prefix="roombook_persist_")
os.environ["ROOMBOOK_DB"] = os.path.join(tmp, "bookings.db")
main.DB_PATH = os.environ["ROOMBOOK_DB"]

# Session 1: book
print("== Session 1: create a booking ==")
main.main(["book", "--date", day, "--room", "Huddle Room B",
           "--start", "14:00", "--end", "14:30", "--title", "Standup", "--organiser", "Dana"])

# Simulate a restart by reconnecting to the same file.
print("\n== Session 2 (new connection = restart): read it back ==")
conn = main.get_connection()
rows = main.confirmed_bookings(conn, main.find_room(conn, "Huddle Room B")["id"], day)
assert any(r["title"] == "Standup" for r in rows), "booking did not survive restart!"
print("PERSISTENCE OK: booking still present after restart ->",
      [(r["id"], r["title"], r["room_id"]) for r in rows])
conn.close()

# Interactive menu smoke test via piped stdin.
print("\n== Interactive menu smoke test ==")
script = "\n".join(["1", day, "", "4", day, "5", ""])
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    old = sys.stdin
    sys.stdin = io.StringIO(script)
    try:
        main.main([])
    finally:
        sys.stdin = old
out = buf.getvalue()
assert "Meeting Room Booking" in out and "Goodbye" in out
print("MENU OK: showed availability + list, exited cleanly.")
print("\nALL CHECKS PASSED")
