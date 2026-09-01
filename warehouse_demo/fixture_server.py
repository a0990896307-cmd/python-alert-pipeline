"""
Local fixture: a tiny HTTP server that mimics a warehouse shift page.
Serves a static HTML table of shifts (id, location, date, time, status).
Used ONLY for local demo/tests. No Amazon access, no credentials.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer

SHIFTS = [
    {"id": "S-001", "location": "YYZ1", "date": "2026-09-03", "time": "07:00", "status": "available"},
    {"id": "S-002", "location": "YYZ1", "date": "2026-09-03", "time": "12:00", "status": "available"},
    {"id": "S-003", "location": "YVR3", "date": "2026-09-03", "time": "07:00", "status": "available"},
]

EXTRA: list[dict] = []  # populated via --extra for the crash-recovery scenario


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        all_shifts = SHIFTS + EXTRA
        rows = "".join(
            f'<tr data-shift-id="{s["id"]}">'
            f'<td class="loc">{s["location"]}</td>'
            f'<td class="date">{s["date"]}</td>'
            f'<td class="time">{s["time"]}</td>'
            f'<td class="status">{s["status"]}</td>'
            f'<td><button class="claim" data-shift-id="{s["id"]}">Claim</button></td>'
            f"</tr>"
            for s in all_shifts
        )
        body = f"""<!doctype html><html><body>
<h1>Shift board (local fixture)</h1>
<table id="shifts">{rows}</table>
</body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, fmt: str, *args) -> None:  # quiet
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--extra", action="append", default=[],
                    help='format "id,location,date,time" — add a shift (crash scenario)')
    args = ap.parse_args()
    for spec in args.extra:
        sid, loc, d, t = spec.split(",")
        EXTRA.append({"id": sid, "location": loc, "date": d, "time": t, "status": "available"})
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()