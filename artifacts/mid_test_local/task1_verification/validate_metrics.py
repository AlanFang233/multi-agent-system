#!/usr/bin/env python3
"""Independent re-derivation of analytics metrics over the real bookings.db."""
import os, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "bookings.db")
print("script:", __file__)
print("db exists:", os.path.exists(DB), DB)
if not os.path.exists(DB):
    import glob
    print("db search:", glob.glob(os.path.join(HERE, "*.db")))
    DB = glob.glob("**/bookings.db", recursive=True)[:1]
    print("fallback:", DB)

conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
conn.row_factory = sqlite3.Row

print("\n== TABLES/INDEXES ==")
for r in conn.execute("SELECT type,name FROM sqlite_master WHERE type IN ('table','index')"):
    print(" ", r["type"], r["name"])

print("\n== ROOMS ==")
for r in conn.execute("SELECT * FROM rooms"):
    print(" ", dict(r))

print("\n== COUNTS / RANGE ==")
print("  total bookings:", conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0])
print("  distinct dates:", conn.execute("SELECT COUNT(DISTINCT date) FROM bookings").fetchone()[0])
row = conn.execute("SELECT MIN(date) mn, MAX(date) mx FROM bookings").fetchone()
print("  date range:", row["mn"], "..", row["mx"])

print("\n== STATUS BREAKDOWN ==")
for r in conn.execute("SELECT status, COUNT(*) n FROM bookings GROUP BY status ORDER BY status"):
    print(" ", r["status"], r["n"])

print("\n== INTEGRITY ==")
for label, sql in [
    ("unknown status", "SELECT COUNT(*) FROM bookings WHERE status NOT IN ('confirmed','cancelled')"),
    ("cancelled w/o cancelled_at", "SELECT COUNT(*) FROM bookings WHERE status='cancelled' AND cancelled_at IS NULL"),
    ("orphan room_id", "SELECT COUNT(*) FROM bookings WHERE room_id NOT IN (SELECT id FROM rooms)"),
    ("bad duration", "SELECT COUNT(*) FROM bookings WHERE end_min-start_min<=0 OR end_min-start_min>240"),
    ("off-boundary", "SELECT COUNT(*) FROM bookings WHERE (start_min-540)%30<>0 OR (end_min-540)%30<>0"),
    ("weekend", "SELECT COUNT(*) FROM bookings WHERE strftime('%w',date) IN ('0','6')"),
    ("out-of-hours", "SELECT COUNT(*) FROM bookings WHERE start_min<540 OR end_min>1080"),
]:
    print("  %s: %d" % (label, conn.execute(sql).fetchone()[0]))

print("\n== DUPLICATE CONFIRMED (same room/date/slot) ==")
dup = conn.execute("""SELECT room_id,date,start_min,end_min,COUNT(*) n FROM bookings
                      WHERE status='confirmed' GROUP BY room_id,date,start_min,end_min HAVING n>1""").fetchall()
print("  dup groups:", len(dup))
for r in dup:
    print("   ", dict(r))

print("\n== OVERLAP among confirmed ==")
ov = 0
for r in conn.execute("SELECT DISTINCT room_id,date FROM bookings WHERE status='confirmed'"):
    rows = conn.execute("SELECT id,start_min,end_min FROM bookings WHERE status='confirmed' AND room_id=? AND date=? ORDER BY start_min",
                        (r["room_id"], r["date"])).fetchall()
    for i in range(len(rows)):
        for j in range(i+1, len(rows)):
            if rows[i]["end_min"] > rows[j]["start_min"]:
                ov += 1
                print("   overlap:", r["room_id"], r["date"], rows[i]["id"], rows[j]["id"])
print("  overlapping pairs:", ov)

print("\n== RAW LISTING ==")
for r in conn.execute("""SELECT b.id, rm.name room, b.date, b.start_min, b.end_min, b.status
                         FROM bookings b JOIN rooms rm ON rm.id=b.room_id
                         ORDER BY b.date, rm.name, b.start_min"""):
    print("  %4d %-14s %s %5d-%5d %s" % (r["id"], r["room"], r["date"], r["start_min"], r["end_min"], r["status"]))

def metrics(lo, hi):
    rows = conn.execute("SELECT b.*, rm.name room FROM bookings b JOIN rooms rm ON rm.id=b.room_id "
                        "WHERE b.date>=? AND b.date<=? AND b.status='confirmed'", (lo, hi)).fetchall()
    per_room, total, per_day, per_slot = {}, 0, {}, {}
    for b in rows:
        total += b["end_min"] - b["start_min"]
        per_room[b["room"]] = per_room.get(b["room"], 0) + 1
        per_day[b["date"]] = per_day.get(b["date"], 0) + 1
        s = b["start_min"]
        while s < b["end_min"]:
            per_slot[s] = per_slot.get(s, 0) + 1; s += 30
    return rows, per_room, total, per_day, per_slot

def show(label, lo, hi):
    rows, per_room, total, per_day, per_slot = metrics(lo, hi)
    alln = conn.execute("SELECT COUNT(*) FROM bookings WHERE date>=? AND date<=?", (lo, hi)).fetchone()[0]
    canc = conn.execute("SELECT COUNT(*) FROM bookings WHERE date>=? AND date<=? AND status='cancelled'", (lo, hi)).fetchone()[0]
    print("\n### RANGE %s..%s [%s]" % (lo, hi, label))
    print("  confirmed=%d rows=%d cancelled=%d cancel_rate=%.4f" % (len(rows), alln, canc, (canc/alln if alln else 0.0)))
    print("  per_room:", per_room)
    print("  total_reserved_min: %d (%.2f h)" % (total, total/60.0))
    print("  per_day:", dict(sorted(per_day.items())))
    if per_day:
        mx = max(per_day.values())
        print("  busiest_day:", [d for d, n in sorted(per_day.items()) if n == mx], "n=", mx)
    if per_slot:
        mx = max(per_slot.values())
        print("  busiest_slot:", ["%02d:%02d" % (s//60, s%60) for s, n in sorted(per_slot.items()) if n == mx], "n=", mx)
    print("  slot_hist:", {("%02d:%02d" % (s//60, s%60)): n for s, n in sorted(per_slot.items())})

span = conn.execute("SELECT MIN(date) mn, MAX(date) mx FROM bookings").fetchone()
print("\n########## METRIC RE-DERIVATION ##########")
show("whole_span", span["mn"], span["mx"])
show("empty_future", "2027-03-01", "2027-03-05")
show("single_day", span["mn"], span["mn"])
show("last_day_only", span["mx"], span["mx"])
conn.close()
print("\nDONE")
