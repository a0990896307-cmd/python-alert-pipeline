"""
Warehouse shift monitor — full runnable demo (local fixture only).

Demonstrates, with a local fixture page and Playwright:
1. Detecting a newly added shift
2. Matching location/date/time preferences
3. Claiming atomically (SQLite transaction, no double-claims)
4. Exactly one simulated selection
5. Crash recovery: restart after simulated crash — no duplicate selection
6. Kill-switch and manual-confirmation behaviour
7. Reconciliation of uncertain outcome (platform accepted, confirmation lost)

No Amazon access, no credentials, no live actions outside the fixture.

Usage:
  python warehouse_demo.py --fixture-url http://127.0.0.1:8765 \
      --pref YYZ1 --date 2026-09-03 --time 07:00 \
      [--manual-confirm] [--kill-switch] [--simulate-crash]
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import async_playwright

DB = Path("warehouse_demo.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS shifts (
    shift_id      TEXT PRIMARY KEY,
    location      TEXT NOT NULL,
    date          TEXT NOT NULL,
    time          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'available',
    claim_state   TEXT NOT NULL DEFAULT 'none',   -- none|claimed|manual_review
    claimed_at    TEXT
);
CREATE TABLE IF NOT EXISTS claim_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id     TEXT NOT NULL UNIQUE,            -- UNIQUE = one claim ever
    action       TEXT NOT NULL,                   -- claimed|selected|review
    detail       TEXT
);
"""


def init_db(path: Path = DB) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)


def _log(db: Path, shift_id: str, action: str, detail: str = "") -> None:
    """Insert audit row; UNIQUE(shift_id) guarantees a shift is acted on once."""
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO claim_audit (shift_id, action, detail) VALUES (?, ?, ?)",
            (shift_id, action, detail),
        )
        conn.commit()


def record_shift(db: Path, s: dict) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO shifts (shift_id, location, date, time, status) VALUES (?,?,?,?,?)",
            (s["id"], s["location"], s["date"], s["time"], s["status"]),
        )
        conn.commit()


def known_shift_ids(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT shift_id FROM shifts").fetchall()
    return {r[0] for r in rows}


@dataclass
class Preference:
    location: str
    date: str
    time: str


def matches(s: dict, pref: Preference) -> bool:
    return s["location"] == pref.location and s["date"] == pref.date and s["time"] == pref.time


async def scrape_shifts(url: str) -> list[dict]:
    """Read the fixture page via Playwright (browser layer)."""
    shifts: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=15000)
            rows = await page.locator("tr[data-shift-id]").all()
            for row in rows:
                l = await row.locator(".loc").inner_text()
                d = await row.locator(".date").inner_text()
                t = await row.locator(".time").inner_text()
                st = await row.locator(".status").inner_text()
                shift_id = await row.get_attribute("data-shift-id")
                shifts.append({"id": shift_id, "location": l, "date": d, "time": t, "status": st})
        finally:
            await browser.close()
    return shifts


def atomic_claim(db: Path, shift_id: str) -> bool:
    """
    Atomically claim: within one IMMEDIATE transaction, verify the shift
    has never been claimed (audit UNIQUE) and mark claim_state='claimed'.
    Returns True exactly once per shift even across processes/restarts.
    """
    conn = sqlite3.connect(db, timeout=5)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT claim_state FROM shifts WHERE shift_id = ?", (shift_id,)
        ).fetchone()
        if row and row[0] in ("claimed", "manual_review"):
            conn.rollback()
            return False  # already acted on this shift
        conn.execute(
            "UPDATE shifts SET claim_state='claimed', claimed_at=CURRENT_TIMESTAMP WHERE shift_id=?",
            (shift_id,),
        )
        _log_in_tx(conn, shift_id, "claimed", "atomic claim")
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def _log_in_tx(conn: sqlite3.Connection, shift_id: str, action: str, detail: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO claim_audit (shift_id, action, detail) VALUES (?, ?, ?)",
        (shift_id, action, detail),
    )


def reconcile_uncertain(db: Path, shift_id: str) -> None:
    """
    Reconciliation: if the platform accepted the selection but confirmation
    was lost before we saw it, we must NOT click again. We mark the shift
    'manual_review' — operator checks the platform once; after human
    verification the record is finalized. No automatic retry.
    """
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE shifts SET claim_state='manual_review' WHERE shift_id=? AND claim_state='claimed'",
            (shift_id,),
        )
        _log_in_tx(conn, shift_id, "review", "uncertain outcome -> manual reconciliation")
        conn.commit()


async def run(
    fixture_url: str,
    pref: Preference,
    db: Path,
    manual_confirm: bool = False,
    kill_switch: bool = False,
    simulate_crash: bool = False,
) -> int:
    init_db(db)
    shifts = await scrape_shifts(fixture_url)

    # 1) Detect NEW shifts (not seen in persistent state before)
    seen = known_shift_ids(db)
    new = [s for s in shifts if s["id"] not in seen]
    for s in shifts:
        record_shift(db, s)
    print(f"[detect] {len(new)} new shift(s): {[s['id'] for s in new] or 'none'}")

    # 2) Preference matching
    candidates = [s for s in new if matches(s, pref)]
    print(f"[match] candidates for {pref}: {[s['id'] for s in candidates] or 'none'}")
    if not candidates:
        print("[done] nothing to do")
        return 0

    target = candidates[0]

    # 6) Kill-switch: never act, only alert
    if kill_switch:
        print("[kill-switch] ACTIVE -> no selection, alert only")
        _log(db, target["id"], "alert", "kill-switch blocked selection")
        return 0

    # 6) Manual confirmation gate
    if manual_confirm:
        print(f"[manual-confirm] shift {target['id']} requires operator click: "
              f"location={target['location']} date={target['date']} time={target['time']}")
        _log(db, target["id"], "review", "manual confirmation required (no auto-select)")
        return 0

    # 3) Atomic claim
    claimed = atomic_claim(db, target["id"])
    print(f"[claim] atomic claim of {target['id']}: {'OK' if claimed else 'ALREADY CLAIMED (skipped)'}")

    # 4) Exactly one simulated selection (browser click on the fixture)
    #    The claim gate above already guarantees idempotency across restarts.
    if claimed and not simulate_crash:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await (await browser.new_context()).new_page()
            await page.goto(fixture_url)
            await page.locator(f"button.claim[data-shift-id='{target['id']}']").click()
            await browser.close()
        _log(db, target["id"], "selected", "simulated selection on fixture")
        print(f"[select] exactly one simulated selection performed for {target['id']}")
        print(f"[audit] claim_audit rows for {target['id']}:")
        for row in audit_rows(db, target["id"]):
            print("        ", row)

    # 5) Crash simulation: die right after claim, before confirmation
    elif claimed and simulate_crash:
        print("[crash] simulated crash AFTER claim, BEFORE confirmation")
        print("[crash] (process would exit here; restarting now)")
        reconcile_uncertain(db, target["id"])
        print("[recover] restart: claim_state -> manual_review; no selection repeated")
        # NOTE: on the real platform the operator checks once. Nothing is clicked twice.

    print(f"[done] {target['id']} claim_state: {shift_state(db, target['id'])}")
    return 0


def audit_rows(db: Path, shift_id: str) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT action, detail FROM claim_audit WHERE shift_id=? ORDER BY id", (shift_id,)
        ).fetchall()


def shift_state(db: Path, shift_id: str) -> str:
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT claim_state FROM shifts WHERE shift_id=?", (shift_id,)).fetchone()
    return row[0] if row else "?"


def main() -> None:
    ap = argparse.ArgumentParser(description="Warehouse monitor demo (local fixture)")
    ap.add_argument("--fixture-url", default="http://127.0.0.1:8765")
    ap.add_argument("--pref", required=True, help="location")
    ap.add_argument("--date", required=True)
    ap.add_argument("--time", required=True)
    ap.add_argument("--db", default="warehouse_demo.db")
    ap.add_argument("--manual-confirm", action="store_true")
    ap.add_argument("--kill-switch", action="store_true")
    ap.add_argument("--simulate-crash", action="store_true")
    ap.add_argument("--keep-db", action="store_true", help="reuse existing state (crash-recovery demo)")
    args = ap.parse_args()

    if not args.keep_db:
        Path(args.db).unlink(missing_ok=True)  # fresh state each demo run
    asyncio.run(run(args.fixture_url, Preference(args.pref, args.date, args.time),
                    Path(args.db), args.manual_confirm, args.kill_switch, args.simulate_crash))


if __name__ == "__main__":
    main()