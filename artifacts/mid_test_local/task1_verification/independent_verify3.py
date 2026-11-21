#!/usr/bin/env python3
"""Check default params / room filter / no-params behavior on the real DB."""
import os, sys, sqlite3, json
from urllib.parse import urlparse
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import analytics_api as api
DB = os.path.join(HERE, "bookings.db")

def call(app, query):
    p = urlparse("/api/dashboard?" + query); cap = {}
    def sr(s, h): cap["s"] = s
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": p.path, "QUERY_STRING": p.query}
    body = b"".join(app(env, sr))
    return cap["s"], json.loads(body.decode())

app = api.create_app(DB)
conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True); conn.row_factory = sqlite3.Row
span = dict(conn.execute("SELECT MIN(date) mn, MAX(date) mx FROM bookings").fetchone())
print("DB span:", span)

s, p = call(app, "")                      # no params -> should default to full span
print("no params          -> status=%s  meta=%s  totals=%s" % (s, p["meta"], p["totals"]))
print("  defaults to full span?", p["meta"]["start_date"] == span["mn"] and p["meta"]["end_date"] == span["mx"])

s, p = call(app, "from=" + span["mn"])    # only from
print("only from          -> status=%s  meta=%s" % (s, p.get("meta")))

s, p = call(app, "room_id=2")
print("room_id=2          -> status=%s  rooms=%s" % (s, [(r["room_id"], r["bookings_confirmed"]) for r in p["rooms"]]))
print("  only room 2 returned?", [r["room_id"] for r in p["rooms"]] == [2])

s, p = call(app, "room_id=abc")
print("room_id=abc        -> status=%s  body=%s" % (s, p))
print("  ->400?", s.startswith("400"))

s, p = call(app, "from=%s&to=%s&room_id=1" % (span["mn"], span["mx"]))
tot1 = p["totals"]
s2, p2 = call(app, "from=%s&to=%s" % (span["mn"], span["mx"]))
r1 = [r for r in p2["rooms"] if r["room_id"] == 1][0]
print("room filter totals == all-range room1 row?",
      tot1["bookings_confirmed"] == r1["bookings_confirmed"],
      tot1["reserved_minutes"] == r1["reserved_minutes"])
