#!/usr/bin/env python3
"""Focused edge-case verification: boundary, zero-length, midnight, reversed."""
import datetime, json, os, sqlite3, sys, tempfile
from urllib.parse import urlparse
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import analytics_api as api

CAP_PER_DAY = 540; WS, WE, SLOT = 540, 1080, 30

def q(conn, sql):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql)]

def oracle(rooms, bookings, start, end):
    def inr(b): return start <= str(b["date"]) <= end
    conf = [b for b in bookings if inr(b) and b["status"] == "confirmed"]
    canc = [b for b in bookings if inr(b) and b["status"] == "cancelled"]
    slots = {m: 0 for m in range(WS, WE, SLOT)}
    for b in conf:
        m = b["start_min"]
        while m < b["end_min"]:
            if m in slots: slots[m] += 1
            m += SLOT
    return {"conf": len(conf), "canc": len(canc),
            "min": sum(b["end_min"] - b["start_min"] for b in conf), "slots": slots}

def call(app, query):
    p = urlparse("/api/dashboard?" + query); cap = {}
    def sr(s, h): cap["s"] = s
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": p.path, "QUERY_STRING": p.query}
    body = b"".join(app(env, sr))
    return cap["s"], json.loads(body.decode())

F = []
def ck(n, e, a):
    ok = (abs(e-a) <= 1e-9) if isinstance(e, float) else (e == a)
    if not ok: F.append((n, e, a))
    print("  [%s] %-40s exp=%r act=%r" % ("PASS" if ok else "FAIL", n, e, a))

tmp = tempfile.mktemp(suffix=".db")
c = sqlite3.connect(tmp)
c.execute("CREATE TABLE rooms(id INTEGER PRIMARY KEY, name TEXT, capacity INT)")
c.execute("CREATE TABLE bookings(id INTEGER PRIMARY KEY, room_id INT, date TEXT, start_min INT, end_min INT, title TEXT, organiser TEXT, status TEXT, created_at TEXT, cancelled_at TEXT)")
c.execute("INSERT INTO rooms VALUES (1,'Boardroom',12)")
recs = [
    (1, "2026-10-05", 540, 600, "confirmed"),   # 1 in-range normal
    (1, "2026-10-04", 540, 600, "confirmed"),   # 2 day before range (Sun)
    (1, "2026-10-10", 540, 600, "confirmed"),   # 3 day after range (Sat)
    (1, "2026-10-06", 0, 60, "confirmed"),      # 4 midnight -> 01:00 (outside work hours)
    (1, "2026-10-06", 0, 0, "confirmed"),       # 5 zero-length at midnight
    (1, "2026-10-07", 600, 600, "confirmed"),   # 6 zero-length at 10:00
    (1, "2026-10-09", 1050, 1080, "confirmed"), # 7 ends exactly at range end day
    (1, "2026-10-05", 540, 600, "cancelled"),   # 8 cancelled in range
]
for i, (r, d, s0, e0, st) in enumerate(recs, 1):
    c.execute("INSERT INTO bookings VALUES (?,?,?,?,?,?,?,?,?,?)",
              (i, r, d, s0, e0, "t", "o", st, "x", None))
c.commit(); c.close()

app = api.create_app(tmp)
bs, be = "2026-10-05", "2026-10-09"
s, p = call(app, "from=%s&to=%s" % (bs, be))
conn = sqlite3.connect("file:%s?mode=ro" % tmp, uri=True)
o = oracle(q(conn, "SELECT id,name,capacity FROM rooms"), q(conn, "SELECT * FROM bookings"), bs, be)
conn.close()
print("=== BOUNDARY / ZERO-LENGTH / MIDNIGHT (range %s..%s) status=%s ===" % (bs, be, s))
print("  oracle:", o["conf"], o["canc"], o["min"])
print("  sut totals:", p["totals"])
ck("totals.confirmed", o["conf"], p["totals"]["bookings_confirmed"])
ck("totals.cancelled", o["canc"], p["totals"]["bookings_cancelled"])
ck("totals.reserved_minutes", o["min"], p["totals"]["reserved_minutes"])
smap = {x["start_min"]: x["bookings_confirmed"] for x in p["by_slot"]}
for m in range(WS, WE, SLOT):
    if o["slots"][m] or smap.get(m):
        ck("by_slot[%d]" % m, o["slots"][m], smap.get(m))

# manual expected: records 1,4,5,6,7 confirmed (5), unaffected by 2,3 (out of range)
print("  manual expected confirmed=5 reserved_min=60+60+0+0+30=150")
ck("manual confirmed==5", 5, p["totals"]["bookings_confirmed"])
ck("manual reserved_min==150", 150, p["totals"]["reserved_minutes"])
os.remove(tmp)

print("\n=== EMPTY-LIST vs MISSING PARAMS ===")
s1, p1 = call(app, "to=2026-11-27")   # only 'to'
print("  only to -> status=%s meta=%s" % (s1, p1["meta"]))
print("\nFAILS:", F if F else "NONE")
