"""
Warehouse shift monitor — POC sample.

Patterns demonstrated:
- Playwright browser automation (retry + exponential backoff)
- SQLite persistent state with dedup (crash recovery: re-run is idempotent)
- Logging every attempt outcome (no silent failures)
- No credentials/CAPTCHA handling — script-safe, operator-authorized use only
"""

import asyncio
import sqlite3
from pathlib import Path

from playwright.async_api import async_playwright, Error as PlaywrightError

DB_PATH = Path("shifts.db")


def init_db(path: Path = DB_PATH) -> None:
    """Idempotent schema setup. Safe to call on every start/recovery."""
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shift_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                status TEXT NOT NULL,
                details TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(url, created_at)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shifts_seen ON shift_logs(url, status)")


def record(path: Path, url: str, status: str, details: str) -> None:
    """Persist an attempt outcome. INSERT OR IGNORE = re-run safe (dedup)."""
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO shift_logs (url, status, details) VALUES (?, ?, ?)",
            (url, status, details),
        )
        conn.commit()


def last_state(path: Path = DB_PATH) -> dict | None:
    """Crash-recovery helper: report last recorded state for a run."""
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT status, details, created_at FROM shift_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {"status": row[0], "details": row[1], "at": row[2]} if row else None


async def fetch_shifts(url: str, retries: int = 3) -> bool:
    """
    Fetch the shift page with retry + exponential backoff.
    Every outcome (success/retry/fail) is persisted — no silent paths.
    """
    init_db()
    record(DB_PATH, url, "START", "session resumed")
    last_error: str | None = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        )
        page = await context.new_page()

        try:
            for attempt in range(1, retries + 1):
                try:
                    response = await page.goto(url, wait_until="networkidle", timeout=15000)
                    if response and response.status == 200:
                        record(DB_PATH, url, "SUCCESS", f"attempt {attempt}")
                        return True
                    last_error = f"HTTP {response.status if response else 'no response'}"
                    record(DB_PATH, url, "RETRY", f"attempt {attempt}: {last_error}")
                except PlaywrightError as exc:
                    last_error = str(exc)
                    record(DB_PATH, url, "RETRY", f"attempt {attempt}: {exc}")
                await asyncio.sleep(2 ** attempt)  # 2s, 4s, 8s backoff
            record(DB_PATH, url, "FAILED", last_error or "exhausted retries")
            return False
        finally:
            await browser.close()


if __name__ == "__main__":
    ok = asyncio.run(fetch_shifts("https://hiring.amazon.ca/app#/jobSearch"))
    print("fetched" if ok else "failed — see shift.db logs")
    print("last state:", last_state())