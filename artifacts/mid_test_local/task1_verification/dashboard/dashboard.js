/* Booking dashboard — read-only analytics UI.
 * Data contract: see DATA_CONTRACT.md (field names + status semantics frozen).
 * Pure vanilla: no external libraries, charts drawn as inline SVG.
 * Exposes: window.BookingDashboard = { CONFIG, mockData, aggregate,
 *           fetchData, renderers... } for testability.
 */
(function (root) {
  "use strict";

  // --- frozen domain constants (mirror main.py / validate_metrics.py) --------
  var WORK_START_MIN = 540, WORK_END_MIN = 1080, SLOT = 30;
  var CAPACITY_MIN_PER_DAY = WORK_END_MIN - WORK_START_MIN; // 540
  var UTIL_LOW = 0.25, UTIL_HIGH = 0.80;
  var WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  var STATUSES = { CONFIRMED: "confirmed", CANCELLED: "cancelled" };

  var CONFIG = {
    mode: "mock",            // "mock" | "live"
    dataUrl: "api/dashboard", // used when mode === "live"
    defaultPreset: "all"
  };

  // --- helpers ---------------------------------------------------------------
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function iso(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function minutesToLabel(m) { return pad(Math.floor(m / 60)) + ":" + pad(m % 60); }
  function isWorkday(isoDate) { var w = new Date(isoDate + "T00:00:00").getDay(); return w >= 1 && w <= 5; }
  function eachDate(startIso, endIso) {
    var out = [], d = new Date(startIso + "T00:00:00"), e = new Date(endIso + "T00:00:00");
    while (d <= e) { out.push(iso(d)); d.setDate(d.getDate() + 1); }
    return out;
  }

  // --- deterministic mock data (same field names as the real store) ----------
  function seed(s) { return function () { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; }; }
  function mockData() {
    var rnd = seed(20261005);
    var rooms = [
      { id: 1, name: "Boardroom", capacity: 12 },
      { id: 2, name: "Meeting Room A", capacity: 8 },
      { id: 3, name: "Huddle Room B", capacity: 4 }
    ];
    var bias = { 1: 0.85, 2: 0.5, 3: 0.18 }; // Boardroom busy, Huddle underused
    var bookings = [], id = 0;
    var start = new Date(); start.setDate(start.getDate() - 56);
    for (var day = 0; day < 56; day++) {
      var d = new Date(start); d.setDate(start.getDate() + day);
      var w = d.getDay(); if (w === 0 || w === 6) continue;
      for (var r = 0; r < rooms.length; r++) {
        var room = rooms[r], load = bias[room.id];
        var n = Math.round((0.6 + rnd() * 1.4) * load * 4);
        for (var k = 0; k < n; k++) {
          var s = WORK_START_MIN + SLOT * Math.floor(rnd() * 16);
          var end = Math.min(s + SLOT * (1 + Math.floor(rnd() * 4)), WORK_END_MIN);
          if (end <= s) continue;
          var cancelled = rnd() < (0.10 + 0.1 * (1 - load));
          id++;
          bookings.push({
            id: id, room_id: room.id, date: iso(d), start_min: s, end_min: end,
            title: "Meeting " + id, organiser: ["Alice", "Bob", "Cara", "Dan"][id % 4],
            status: cancelled ? STATUSES.CANCELLED : STATUSES.CONFIRMED,
            created_at: iso(d) + "T08:00:00", cancelled_at: cancelled ? iso(d) + "T08:30:00" : null
          });
        }
      }
    }
    return { rooms: rooms, bookings: bookings };
  }

  // --- aggregation (implements DATA_CONTRACT.md section 3 exactly) -----------
  function aggregate(input, range, roomId) {
    var rooms = input.rooms || [], bookings = input.bookings || [];
    var startDate = range.start_date, endDate = range.end_date;
    if (roomId === undefined) roomId = null;
    var filtered = bookings.filter(function (b) {
      if (b.date < startDate || b.date > endDate) return false;      // inclusive
      if (roomId != null && b.room_id !== roomId) return false;
      return true;
    });
    var dates = eachDate(startDate, endDate);
    var workdays = dates.filter(isWorkday).length;
    var denom = workdays * CAPACITY_MIN_PER_DAY;

    var roomRows = {}, dayRows = {}, slot = {};
    var wd = []; for (var i = 0; i < 5; i++) wd.push({ weekday: WEEKDAYS[i], weekday_index: i + 1, bookings_confirmed: 0, reserved_minutes: 0 });
    for (var m = WORK_START_MIN; m < WORK_END_MIN; m += SLOT) slot[m] = { start_min: m, slot_label: minutesToLabel(m), bookings_confirmed: 0 };

    var tot = { bookings_confirmed: 0, bookings_cancelled: 0, reserved_minutes: 0 };
    rooms.forEach(function (rm) {
      if (roomId != null && rm.id !== roomId) return;
      roomRows[rm.id] = { room_id: rm.id, room_name: rm.name, capacity: rm.capacity,
        bookings_confirmed: 0, bookings_cancelled: 0, reserved_minutes: 0 };
    });

    filtered.forEach(function (b) {
      var row = roomRows[b.room_id];
      if (b.status !== STATUSES.CONFIRMED) return;                  // only confirmed occupies
      var mins = b.end_min - b.start_min;
      if (row) { row.bookings_confirmed++; row.reserved_minutes += mins; }
      tot.bookings_confirmed++; tot.reserved_minutes += mins;
      var dd = (dayRows[b.date] = dayRows[b.date] || { date: b.date, bookings_confirmed: 0, reserved_minutes: 0, bookings_cancelled: 0 });
      dd.bookings_confirmed++; dd.reserved_minutes += mins;
      var wIdx = new Date(b.date + "T00:00:00").getDay() - 1;
      if (wIdx >= 0 && wIdx < 5) { wd[wIdx].bookings_confirmed++; wd[wIdx].reserved_minutes += mins; }
      for (var t = b.start_min; t < b.end_min; t += SLOT) if (slot[t]) slot[t].bookings_confirmed++;
    });
    filtered.forEach(function (b) {
      var row = roomRows[b.room_id];
      if (b.status === STATUSES.CANCELLED) {
        if (row) row.bookings_cancelled++;
        var dd = dayRows[b.date]; if (dd) dd.bookings_cancelled++;
      }
    });

    var roomList = Object.keys(roomRows).map(function (k) {
      var r = roomRows[k];
      r.reserved_hours = +(r.reserved_minutes / 60).toFixed(2);
      r.utilisation = denom ? r.reserved_minutes / denom : 0;
      var all = r.bookings_confirmed + r.bookings_cancelled;
      r.cancellation_rate = all ? r.bookings_cancelled / all : 0;
      r.under_utilised = r.utilisation < UTIL_LOW;
      r.over_utilised = r.utilisation > UTIL_HIGH;
      return r;
    });
    tot.bookings_cancelled = filtered.filter(function (b) { return b.status === STATUSES.CANCELLED; }).length;
    tot.reserved_hours = +(tot.reserved_minutes / 60).toFixed(2);
    var allTot = tot.bookings_confirmed + tot.bookings_cancelled;
    tot.cancellation_rate = allTot ? tot.bookings_cancelled / allTot : 0;

    var ordered = roomList.slice().sort(function (a, b2) { return b2.utilisation - a.utilisation; });
    return {
      meta: { start_date: startDate, end_date: endDate, room_count: roomList.length,
              workdays: workdays, capacity_minutes_per_day: CAPACITY_MIN_PER_DAY },
      rooms: roomList,
      totals: tot,
      by_day: Object.keys(dayRows).sort().map(function (k) { return dayRows[k]; }),
      by_weekday: wd,
      by_slot: Object.keys(slot).map(function (k) { return slot[k]; }),
      summary: {
        most_used_rooms: ordered.slice(0, 3).map(function (r) { return { room_id: r.room_id, room_name: r.room_name, utilisation: r.utilisation }; }),
        least_used_rooms: ordered.slice(-3).reverse().map(function (r) { return { room_id: r.room_id, room_name: r.room_name, utilisation: r.utilisation }; })
      }
    };
  }

  // --- data source (mock | live) --------------------------------------------
  function fetchData(opts) {
    opts = opts || {};
    if (CONFIG.mode !== "live" || opts.forceMock) return Promise.resolve(mockData());
    return fetch(CONFIG.dataUrl, { headers: { Accept: "application/json" } })
      .then(function (res) { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
      .then(function (payload) {
        // Accept either a raw {rooms,bookings} store or the aggregated payload; the
        // app normalises both, so no backend change is needed either way.
        return payload;
      });
  }

  // --- SVG chart renderers ---------------------------------------------------
  var NS = "http://www.w3.org/2000/svg";
  function el(tag, attrs, text) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]; }); }

  function chartFrame(mount, title, w, h) {
    mount.innerHTML = "";
    if (title) { var t = document.createElement("div"); t.className = "chart-title"; t.textContent = title; mount.appendChild(t); }
    var svg = el("svg", { viewBox: "0 0 " + w + " " + h, class: "chart", role: "img", "aria-label": title || "chart", preserveAspectRatio: "xMidYMid meet" });
    mount.appendChild(svg);
    return svg;
  }

  function barChart(mount, opts) {
    var data = opts.data || [], title = opts.title, unit = opts.unit || "";
    var w = 640, h = 260, pad = { l: 56, r: 16, t: 12, b: 58 };
    var svg = chartFrame(mount, title, w, h);
    if (!data.length) { mount.insertAdjacentHTML("beforeend", '<p class="empty">No data for this selection.</p>'); return; }
    var iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
    var max = Math.max.apply(null, data.map(function (d) { return d.value; }).concat([1]));
    data.forEach(function (d, i) {
      var bw = iw / data.length, x = pad.l + i * bw + bw * 0.15, bh = ih * d.value / max;
      var g = el("g", {});
      g.appendChild(el("rect", { x: x, y: pad.t + ih - bh, width: Math.max(bw * 0.7, 1), height: Math.max(bh, d.value > 0 ? 1 : 0), rx: 3, fill: opts.color || "#2563eb" }));
      g.appendChild(el("text", { x: x + bw * 0.35, y: pad.t + ih - bh - 4, "text-anchor": "middle", "font-size": 11, fill: "#1f2937" }, String(d.value)));
      g.appendChild(el("text", { x: x + bw * 0.35, y: h - pad.b + 18, "text-anchor": "middle", "font-size": 11, fill: "#374151" }, d.label));
      svg.appendChild(g);
    });
    svg.appendChild(el("line", { x1: pad.l, y1: pad.t + ih, x2: w - pad.r, y2: pad.t + ih, stroke: "#9ca3af" }));
    svg.appendChild(el("text", { x: 8, y: 16, "font-size": 11, fill: "#6b7280" }, (opts.yLabel || "") + (unit ? " (" + unit + ")" : "")));
  }

  function lineChart(mount, opts) {
    var data = opts.data || [], title = opts.title;
    var w = 640, h = 260, pad = { l: 56, r: 16, t: 12, b: 58 };
    var svg = chartFrame(mount, title, w, h);
    if (!data.length) { mount.insertAdjacentHTML("beforeend", '<p class="empty">No data for this selection.</p>'); return; }
    var iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
    var max = Math.max.apply(null, data.map(function (d) { return d.value; }).concat([1]));
    var step = data.length > 1 ? iw / (data.length - 1) : 0;
    var pts = data.map(function (d, i) { return [pad.l + i * step, pad.t + ih - ih * d.value / max]; });
    svg.appendChild(el("polyline", { points: pts.map(function (p) { return p.join(","); }).join(" "), fill: "none", stroke: opts.color || "#2563eb", "stroke-width": 2 }));
    var every = Math.max(1, Math.ceil(data.length / 8));
    pts.forEach(function (p, i) {
      if (i % every === 0 || i === data.length - 1) {
        svg.appendChild(el("circle", { cx: p[0], cy: p[1], r: 3, fill: opts.color || "#2563eb" }));
        svg.appendChild(el("text", { x: p[0], y: h - pad.b + 18, "text-anchor": "middle", "font-size": 10, fill: "#374151" }, data[i].label));
      }
    });
    svg.appendChild(el("line", { x1: pad.l, y1: pad.t + ih, x2: w - pad.r, y2: pad.t + ih, stroke: "#9ca3af" }));
    svg.appendChild(el("text", { x: 8, y: 16, "font-size": 11, fill: "#6b7280" }, opts.yLabel || ""));
  }

  function heatmap(mount, opts) {
    var grid = opts.grid || [], hours = opts.hours || [], title = opts.title;
    mount.innerHTML = "";
    if (title) { var t = document.createElement("div"); t.className = "chart-title"; t.textContent = title; mount.appendChild(t); }
    var max = 1; grid.forEach(function (r) { r.forEach(function (v) { if (v > max) max = v; }); });
    var table = document.createElement("table"); table.className = "heatmap";
    var thead = document.createElement("thead"), tr = document.createElement("tr");
    tr.appendChild(document.createElement("th"));
    hours.forEach(function (hh) { var th = document.createElement("th"); th.textContent = hh; tr.appendChild(th); });
    thead.appendChild(tr); table.appendChild(thead);
    var tb = document.createElement("tbody");
    WEEKDAYS.forEach(function (d, i) {
      var row = document.createElement("tr"), th = document.createElement("th"); th.textContent = d; row.appendChild(th);
      hours.forEach(function (_, j) {
        var v = (grid[i] && grid[i][j]) || 0, td = document.createElement("td");
        var alpha = v / max; td.style.background = "rgba(37,99,235," + (0.08 + 0.82 * alpha).toFixed(2) + ")";
        td.style.color = alpha > 0.6 ? "#fff" : "#111827"; td.textContent = v || "";
        td.title = d + " " + hours[j] + " — " + v + " confirmed";
        row.appendChild(td);
      });
      tb.appendChild(row);
    });
    table.appendChild(tb); mount.appendChild(table);
    var lg = document.createElement("div"); lg.className = "legend";
    lg.innerHTML = "<span>Fewer</span><i style='background:rgba(37,99,235,.08)'></i><i style='background:rgba(37,99,235,.45)'></i><i style='background:rgba(37,99,235,.9)'></i><span>More</span>";
    mount.appendChild(lg);
  }

  function renderTable(mount, columns, rows) {
    mount.innerHTML = "";
    if (!rows.length) { mount.innerHTML = '<p class="empty">No rows for this selection.</p>'; return; }
    var t = document.createElement("table"); t.className = "data-table";
    t.innerHTML = "<thead><tr>" + columns.map(function (c) { return "<th>" + esc(c.label) + "</th>"; }).join("") + "</tr></thead>" +
      "<tbody>" + rows.map(function (r) {
        return "<tr>" + columns.map(function (c) { return "<td>" + esc(r[c.key]) + "</td>"; }).join("") + "</tr>";
      }).join("") + "</tbody>";
    mount.appendChild(t);
  }

  var API = {
    CONFIG: CONFIG, mockData: mockData, aggregate: aggregate, fetchData: fetchData,
    barChart: barChart, lineChart: lineChart, heatmap: heatmap, renderTable: renderTable,
    WEEKDAYS: WEEKDAYS, minutesToLabel: minutesToLabel, eachDate: eachDate,
    constants: { WORK_START_MIN: WORK_START_MIN, WORK_END_MIN: WORK_END_MIN, SLOT: SLOT,
                 CAPACITY_MIN_PER_DAY: CAPACITY_MIN_PER_DAY, UTIL_LOW: UTIL_LOW, UTIL_HIGH: UTIL_HIGH }
  };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  root.BookingDashboard = API;
})(typeof window !== "undefined" ? window : this);
