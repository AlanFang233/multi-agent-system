import os, hashlib, sqlite3
HERE = os.path.dirname(os.path.abspath(__file__))
def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()
print("== ARTIFACT HASHES (SHA256) ==")
for fn in ["bookings.db", "main.py", "boundary_test.py", "demo.py", "README.md"]:
    p = os.path.join(HERE, fn)
    print("  %-18s %s  (%d bytes)" % (fn, sha(p)[:32], os.path.getsize(p)))

print("\n== ANALYTICS MODULE PRESENCE ==")
cands = [f for f in os.listdir(HERE)
         if any(k in f.lower() for k in ["metric","analytic","dashboard","chart","report"]) 
         and f.endswith(".py") and f not in ("validate_metrics.py","scan_modules.py")]
print("  candidate analytics .py files:", cands or "NONE")

conn = sqlite3.connect("file:%s?mode=ro" % os.path.join(HERE,"bookings.db"), uri=True)
conn.row_factory = sqlite3.Row
print("\n== PER-ROOM CANCEL RATES (whole span) ==")
for r in conn.execute("""SELECT rm.name,
        SUM(CASE WHEN b.status='confirmed' THEN 1 ELSE 0 END) conf,
        SUM(CASE WHEN b.status='cancelled' THEN 1 ELSE 0 END) canc,
        COUNT(*) tot
        FROM bookings b JOIN rooms rm ON rm.id=b.room_id GROUP BY rm.name ORDER BY rm.name"""):
    print("  %-14s conf=%d canc=%d tot=%d rate=%.3f" % (r["name"], r["conf"], r["canc"], r["tot"], r["canc"]/r["tot"]))

print("\n== WINDOW TEST: 2026-11-24..2026-11-25 (boundary interior) ==")
for r in conn.execute("""SELECT b.date, rm.name, b.start_min, b.end_min, b.status FROM bookings b
                         JOIN rooms rm ON rm.id=b.room_id WHERE b.date>='2026-11-24' AND b.date<='2026-11-25'
                         ORDER BY b.date, rm.name"""):
    print("  ", r["date"], r["name"], r["start_min"], r["end_min"], r["status"])

print("\n== DST/TZ note check ==")
print("  HK has NO DST; dates 2026-11-23..27 are Mon..Fri, all workdays, no DST transition in HK.")
print("  (The only DST concern would be if dates were parsed as UTC; they are stored as naive local strings.)")
conn.close()
print("DONE")
