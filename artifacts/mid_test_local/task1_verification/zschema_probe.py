import sqlite3, os, glob
cands = glob.glob("/work/*.db") + glob.glob("/work/**/*.db", recursive=True)
print("dbs", cands, flush=True)
p = cands[0] if cands else None
if p:
    con = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
    for t in ("rooms", "bookings"):
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]
            print(t, cols, flush=True)
        except Exception as e:
            print(t, "ERR", e, flush=True)
    print("rooms_rows", list(con.execute("SELECT id,name,capacity FROM rooms")), flush=True)
    print("statuses", list(con.execute("SELECT status, COUNT(*) FROM bookings GROUP BY status")), flush=True)
    print("span", list(con.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM bookings")), flush=True)
