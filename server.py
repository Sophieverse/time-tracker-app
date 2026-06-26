#!/usr/bin/env python3
"""Local dashboard server.

Serves the single-page dashboard (dashboard.html) and a small JSON API derived
live from the SQLite event log. Stdlib only.

  python3 server.py          # serve on :7799 (set TT_NO_OPEN=1 to not open a tab)
  python3 server.py 8080     # custom port

Endpoints:
  GET /                  → dashboard.html
  GET /api/days          → ["2026-06-26", ...]
  GET /api/categories    → {"taxonomy":[...]}
  GET /api/day?date=…    → full day summary (totals, focus, breakdowns, timeline)
  GET /api/range?days=7  → per-day rollups for the trends chart
"""
from __future__ import annotations

import json
import os
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import analytics
import db
import taxonomy

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_PATH = os.path.join(HERE, "dashboard.html")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == "/" or u.path == "/index.html":
                self._serve_page()
            elif u.path == "/api/days":
                conn = db.connect()
                try:
                    self._json(analytics.available_days(conn))
                finally:
                    conn.close()
            elif u.path == "/api/categories":
                self._json({"taxonomy": taxonomy.taxonomy_list()})
            elif u.path == "/api/day":
                date = parse_qs(u.query).get("date", [""])[0]
                if not DATE_RE.match(date):
                    from datetime import datetime
                    date = datetime.now().strftime("%Y-%m-%d")
                conn = db.connect()
                try:
                    self._json(analytics.day_summary(conn, date))
                finally:
                    conn.close()
            elif u.path == "/api/range":
                days = parse_qs(u.query).get("days", ["7"])[0]
                try:
                    days = max(1, min(31, int(days)))
                except ValueError:
                    days = 7
                conn = db.connect()
                try:
                    self._json(analytics.range_summary(conn, days))
                finally:
                    conn.close()
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)

    def _serve_page(self):
        try:
            with open(PAGE_PATH, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        except FileNotFoundError:
            self._send(200, "<h1>dashboard.html not found</h1>", "text/html")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7799
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    print(f"[dashboard] serving {url}  (db: {db.DB_PATH})")
    if not os.environ.get("TT_NO_OPEN"):
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
