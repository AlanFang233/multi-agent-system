import os, re
HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = ["metric", "analytic", "dashboard", "chart", "busiest", "cancel_rate",
        "cancellation", "reserved", "utilization", "utilisation", "aggregate",
        "report", "summary", "per_room", "time_slot", "matplotlib", "plot"]
for fn in sorted(os.listdir(HERE)):
    if not fn.endswith(".py"):
        continue
    p = os.path.join(HERE, fn)
    src = open(p, encoding="utf-8", errors="replace").read()
    defs = re.findall(r"^def\s+(\w+)", src, re.M)
    hits = sorted({k for k in KEYS if k.lower() in src.lower()})
    print("== %s (%d chars) ==" % (fn, len(src)))
    print("   defs:", defs)
    print("   keyword hits:", hits)

print("\n== README/FIX_REPORT first lines ==")
for fn in ["README.md", "FIX_REPORT.md", "HOW-TO-USE.md"]:
    p = os.path.join(HERE, fn)
    if os.path.exists(p):
        print("---", fn)
        print("\n".join(open(p, encoding="utf-8", errors="replace").read().splitlines()[:15]))
