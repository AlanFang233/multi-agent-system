#!/usr/bin/env python3
"""Dashboard front end.

Renders the four metric groups returned by metrics_engine (it does NOT
recompute anything):
  1. Bookings per room (usage frequency)
  2. Total time reserved
  3. Busiest days and times
  4. Cancellation rate
Usable as a text report and as a tiny local HTTP service (no external deps).
"""
import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import booking_store as bs
import metrics_engine as me

NAV = [("2026-11-23", "2026-11-23", "Mon (single day)"),
       ("2026-11-23", "2026-11-27", "Mon-Fri (whole week)"),
       ("2026-11-25", "2026-11-27", "Wed-Fri"),
       ("2027-03-01", "2027-03-05", "Empty future week")]


def _bar(n, mx, width=30, ch="#"):
    if mx <= 0:
        return ""
    return ch * max(1, int(round(n / mx * width))) if n else ""


def render_text(m, active=None):
    out = []
    rng = m["date_range"]
    out.append("=" * 68)
    out.append(" MEETING ROOM DASHBOARD   range %s .. %s" % (rng["start"], rng["end"]))
    out.append("=" * 68)
    if active:
        out.append(" date-range selector: " + " | ".join(
            ("[%s]" if i == active else " %s ") % lbl for i, (_, _, lbl) in enumerate(NAV)))
    out.append("")
    # 1. per room
    out.append("1) BOOKINGS PER ROOM (confirmed)")
    mx = max(m["per_room"].values()) if m["per_room"] else 0
    for room, n in m["per_room"].items():
        out.append("   %-16s %3d  %s" % (room, n, _bar(n, mx)))
    out.append("")
    # 2. reserved time
    out.append("2) TOTAL TIME RESERVED")
    out.append("   %d bookings reserved %d min = %s hours"
               % (m["confirmed_count"], m["total_reserved_minutes"], m["total_reserved_hours"]))
    out.append("")
    # 3. busiest days/times
    out.append("3) BUSIEST DAYS AND TIMES")
    dmax = m["per_day_max"]
    for d, n in m["per_day"].items():
        out.append("   %-12s %3d  %s" % (d, n, _bar(n, dmax)))
    if not m["per_day"]:
        out.append("   (no bookings in range)")
    out.append("   busiest day(s): %s (%d bookings)" % (m["busiest_days"] or "-", dmax))
    if m["per_slot"]:
        out.append("   30-min slot histogram:")
        for s, n in m["per_slot"].items():
            out.append("     %-6s %3d  %s" % (s, n, _bar(n, m["per_slot_max"], width=20, ch="*")))
        out.append("   busiest slot(s): %s (%d overlaps)" % (m["busiest_slots"] or "-", m["per_slot_max"]))
    out.append("")
    # 4. cancellation
    out.append("4) CANCELLATIONS")
    out.append("   cancelled %d of %d bookings  ->  cancel rate %.0f%%"
               % (m["cancelled_count"], m["total_rows"], m["cancel_rate"] * 100))
    out.append("")
    return "\n".join(out)


def render_html(m, active=None):
    rng = m["date_range"]
    def barchart(d):
        if not d:
            return "<p><em>no data</em></p>"
        mx = max(d.values()) or 1
        rows = "".join(
            '<div class="row"><span class="lbl">%s</span>'
            '<span class="bar" style="width:%d%%"></span><span class="val">%d</span></div>'
            % (html.escape(k), int(v / mx * 100), v) for k, v in d.items())
        return rows
    sel = " | ".join(
        '<a href="/?start=%s&end=%s"%s>%s</a>'
        % (s, e, ' class="active"' if i == active else "", html.escape(lbl))
        for i, (s, e, lbl) in enumerate(NAV))
    return """<!doctype html><html><head><meta charset="utf-8">
<title>Meeting Room Dashboard</title>
<style>body{{font-family:sans-serif;margin:2rem;max-width:900px}}
.row{{margin:2px 0}} .lbl{{display:inline-block;width:160px}}
.bar{{display:inline-block;height:14px;background:#4a7;vertical-align:middle}}
.val{{margin-left:8px}} h2{{margin-top:1.4rem}} nav a{{margin-right:6px}}
nav a.active{{font-weight:bold;text-decoration:none}}</style></head><body>
<h1>Meeting Room Dashboard</h1>
<nav>{sel}</nav>
<h2>Range {start} .. {end}</h2>
<h2>1) Bookings per room</h2>{room}
<h2>2) Total time reserved</h2>
<p>{reserved} bookings reserved {mins} min = {hrs} hours</p>
<h2>3) Busiest days and times</h2>
<p>Busiest day(s): <b>{bdays}</b> ({dmax} bookings)</p>{days}
<p>30-minute slot histogram:</p>{slots}
<p>Busiest slot(s): <b>{bslots}</b> ({smax} overlaps)</p>
<h2>4) Cancellations</h2>
<p>Cancelled {canc} of {tot} bookings &rarr; cancel rate {rate:.0f}%</p>
</body></html>""".format(
        sel=sel, start=rng["start"], end=rng["end"],
        room=barchart(m["per_room"]), reserved=m["confirmed_count"],
        mins=m["total_reserved_minutes"], hrs=m["total_reserved_hours"],
        days=barchart(m["per_day"]), bdays=", ".join(m["busiest_days"]) or "-", dmax=m["per_day_max"],
        slots=barchart(m["per_slot"]), bslots=", ".join(m["busiest_slots"]) or "-", smax=m["per_slot_max"],
        canc=m["cancelled_count"], tot=m["total_rows"], rate=m["cancel_rate"] * 100)


def make_handler(db_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            start = q.get("start", ["2026-11-23"])[0]
            end = q.get("end", [start])[0]
            m = me.metrics_for_db(db_path, start, end)
            body = render_html(m).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    return Handler


def serve(db_path, host="127.0.0.1", port=8765):
    httpd = HTTPServer((host, port), make_handler(db_path))
    return httpd


def main():
    p = argparse.ArgumentParser(description="Meeting room dashboard")
    p.add_argument("--db", default=bs.DEFAULT_DB)
    p.add_argument("--start", default="2026-11-23")
    p.add_argument("--end", default="2026-11-27")
    p.add_argument("--serve", action="store_true")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    if args.serve:
        srv = serve(args.db, port=args.port)
        print("dashboard serving on http://127.0.0.1:%d  (Ctrl+C to stop)" % args.port)
        srv.serve_forever()
        return
    print(render_text(me.metrics_for_db(args.db, args.start, args.end)))


if __name__ == "__main__":
    main()
