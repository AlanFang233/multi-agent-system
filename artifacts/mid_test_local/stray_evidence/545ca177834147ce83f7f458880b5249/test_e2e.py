"""End-to-end integration test: booking records -> metrics -> dashboard."""
import os
import tempfile

import booking_store as bs
import metrics_engine as me
import dashboard_ui as ui

GROUPS = ["1) Bookings per room", "2) Total time reserved",
          "3) Busiest days and times", "4) Cancellations"]


def _db():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "bookings.db")
    bs.init_db(p)
    return p


def test_metrics_contract_week():
    m = me.metrics_for_db(_db(), "2026-11-23", "2026-11-27")
    assert m["confirmed_count"] == 14
    assert m["cancelled_count"] == 3
    assert m["total_rows"] == 17
    assert abs(m["cancel_rate"] - 3 / 17) < 1e-9
    assert sum(m["per_room"].values()) == 14
    assert m["per_day"]["2026-11-24"] == 5        # busiest day
    assert m["busiest_days"] == ["2026-11-24"]
    assert m["busiest_slots"] == ["09:00"]        # 3 concurrent starts
    assert m["per_slot_max"] == 3


def test_ui_renders_four_groups_all_ranges():
    db = _db()
    for s, e, _ in ui.NAV:
        html = ui.render_html(me.metrics_for_db(db, s, e))
        for g in GROUPS:
            assert g in html, "missing group %r for %s..%s" % (g, s, e)


def test_empty_range_no_crash():
    m = me.metrics_for_db(_db(), "2027-03-01", "2027-03-05")
    assert m["confirmed_count"] == 0
    assert m["total_reserved_minutes"] == 0
    assert ui.render_text(m)


def test_dashboard_updates_between_ranges():
    db = _db()
    a = me.metrics_for_db(db, "2026-11-23", "2026-11-23")
    b = me.metrics_for_db(db, "2026-11-23", "2026-11-27")
    assert a["confirmed_count"] != b["confirmed_count"]
