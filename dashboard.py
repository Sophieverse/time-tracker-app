#!/usr/bin/env python3
"""Local dashboard for the Time Tracker.

Reads the daily JSON logs the agent writes to data/ and serves a small
single-page dashboard on http://localhost:7799. Stdlib only.

  python3 dashboard.py          # serve on :7799
  python3 dashboard.py 8080     # serve on a custom port

The page polls every 15s, so it mirrors the live tracker within seconds
(the agent rewrites today's JSON on every sample).
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── data access ──────────────────────────────────────────────────────────────

def list_days() -> list[str]:
    days = []
    for p in glob.glob(os.path.join(DATA_DIR, "*.json")):
        name = os.path.basename(p)[:-5]
        if DATE_RE.match(name):
            days.append(name)
    return sorted(days, reverse=True)


def read_day(date: str) -> dict:
    if not DATE_RE.match(date):
        return {"date": date, "domains": {}}
    try:
        with open(os.path.join(DATA_DIR, f"{date}.json")) as f:
            return json.load(f)
    except Exception:
        return {"date": date, "domains": {}}


# ── HTTP ─────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif u.path == "/api/days":
            self._send(200, json.dumps(list_days()))
        elif u.path == "/api/day":
            qs = parse_qs(u.query)
            date = qs.get("date", [""])[0]
            self._send(200, json.dumps(read_day(date)))
        else:
            self._send(404, json.dumps({"error": "not found"}))


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Time Tracker</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0d0d0f; color: #f2f2f2;
    padding: 32px; max-width: 760px; margin: 0 auto;
  }
  header { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 4px; }
  h1 { font-size: 20px; font-weight: 650; letter-spacing: .01em; }
  .pulse { font-size: 11px; color: #4ecb71; }
  .sub { font-size: 12px; color: #6a6a72; margin-bottom: 22px; }

  .controls { display: flex; gap: 10px; align-items: center; margin-bottom: 20px; }
  select {
    background: #18181c; color: #f2f2f2; border: 1px solid #2c2c33;
    border-radius: 8px; padding: 7px 12px; font-size: 13px; outline: none;
  }
  .total {
    margin-left: auto; font-size: 13px; color: #aaa;
  }
  .total b { color: #fff; font-size: 15px; }

  ul { list-style: none; }
  .row { display: flex; align-items: center; gap: 12px; padding: 9px 0; border-bottom: 1px solid #19191e; }
  .rank { font-size: 11px; color: #43434c; width: 18px; text-align: right; }
  .domain { width: 200px; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .barwrap { flex: 1; height: 8px; background: #18181c; border-radius: 4px; overflow: hidden; }
  .bar { height: 8px; border-radius: 4px; background: linear-gradient(90deg,#4f8ef7,#7b6cff); }
  .time { width: 64px; text-align: right; font-size: 12px; color: #c9c9d2; font-variant-numeric: tabular-nums; }

  .empty { color: #5a5a62; font-size: 13px; text-align: center; padding: 48px 0; }
  footer { margin-top: 28px; font-size: 11px; color: #43434c; text-align: center; }
  a { color: #4f8ef7; text-decoration: none; }
</style>
</head>
<body>
  <header>
    <h1>Time Tracker</h1>
    <span class="pulse" id="pulse">● live</span>
  </header>
  <div class="sub" id="updated">—</div>

  <div class="controls">
    <select id="day"></select>
    <span class="total" id="total"></span>
  </div>

  <ul id="list"></ul>
  <div class="empty" id="empty" style="display:none">No activity logged for this day.</div>

  <footer>Reading <code>data/&lt;date&gt;.json</code> · refreshes every 15s</footer>

<script>
const fmt = s => {
  s = Math.round(s);
  if (s < 60) return s + "s";
  const m = Math.floor(s/60), h = Math.floor(m/60);
  return h > 0 ? `${h}h ${m%60}m` : `${m}m`;
};

let current = null;

async function loadDays() {
  const days = await (await fetch("/api/days")).json();
  const sel = document.getElementById("day");
  const prev = sel.value;
  sel.innerHTML = "";
  if (days.length === 0) { current = null; render({date:"", domains:{}}); return; }
  for (const d of days) {
    const o = document.createElement("option");
    const dt = new Date(d + "T00:00:00");
    o.value = d;
    o.textContent = dt.toLocaleDateString("en-US",{weekday:"short",month:"short",day:"numeric"});
    sel.appendChild(o);
  }
  sel.value = (prev && days.includes(prev)) ? prev : days[0];
  current = sel.value;
  loadDay();
}

async function loadDay() {
  if (!current) return;
  const data = await (await fetch("/api/day?date=" + current)).json();
  render(data);
}

function render(data) {
  const entries = Object.entries(data.domains || {}).sort((a,b)=>b[1]-a[1]);
  const total = entries.reduce((s,[,v])=>s+v,0);
  const max = entries.length ? entries[0][1] : 1;
  const list = document.getElementById("list");
  const empty = document.getElementById("empty");
  list.innerHTML = "";

  if (!entries.length) {
    empty.style.display = "block";
    document.getElementById("total").innerHTML = "";
  } else {
    empty.style.display = "none";
    entries.forEach(([dom, secs], i) => {
      const li = document.createElement("li");
      li.className = "row";
      li.innerHTML = `<span class="rank">${i+1}</span>
        <span class="domain" title="${dom}">${dom}</span>
        <div class="barwrap"><div class="bar" style="width:${Math.max(2,Math.round(secs/max*100))}%"></div></div>
        <span class="time">${fmt(secs)}</span>`;
      list.appendChild(li);
    });
    document.getElementById("total").innerHTML = `Total <b>${fmt(total)}</b>`;
  }
  document.getElementById("updated").textContent =
    data.updated ? ("Last updated " + new Date(data.updated).toLocaleTimeString()) : "—";
}

document.getElementById("day").addEventListener("change", e => { current = e.target.value; loadDay(); });

loadDays();
setInterval(() => { loadDays(); }, 15000);
</script>
</body>
</html>
"""


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7799
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    print(f"[dashboard] serving {url}  (reading {DATA_DIR})")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] stopped")


if __name__ == "__main__":
    main()
