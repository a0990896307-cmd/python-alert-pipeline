#!/usr/bin/env python3
"""Scenario: exactly-one-selection across a simulated crash.

1. Fresh DB, shift S-001 -> auto-claimed and selected once.
2. Restart (same DB) -> S-001 already known, nothing done.
3. New shift S-004 appears. Bot claims it, then CRASH before confirmation.
4. Restart -> S-004 must NOT be selected again (reconciliation state).
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
URL = "http://127.0.0.1:8765"
DB = HERE / "warehouse_demo.db"


def run_demo(*args: str) -> str:
    p = subprocess.run(
        [sys.executable, str(HERE / "warehouse_demo.py"), "--fixture-url", URL, *args],
        capture_output=True, text=True, timeout=60,
    )
    return p.stdout + p.stderr


def main() -> int:
    DB.unlink(missing_ok=True)

    server = subprocess.Popen(
        [sys.executable, str(HERE / "fixture_server.py"), "--port", "8765"], cwd=HERE
    )
    time.sleep(1.5)

    print("=== STEP 1: S-001 normal auto-select (fresh DB) ===")
    print(run_demo("--pref", "YYZ1", "--date", "2026-09-03", "--time", "07:00"))

    print("=== STEP 2: restart, same DB — S-001 already claimed, no repeat ===")
    print(run_demo("--pref", "YYZ1", "--date", "2026-09-03", "--time", "07:00", "--keep-db"))

    # New shift becomes available: S-004 (morning, YYZ1)
    server.terminate()
    server.wait()
    server = subprocess.Popen(
        [sys.executable, str(HERE / "fixture_server.py"), "--port", "8765",
         "--extra", "S-004,YYZ1,2026-09-03,07:00"], cwd=HERE
    )
    time.sleep(1.5)

    print("=== STEP 3: S-004 claimed, CRASH before confirmation ===")
    print(run_demo("--pref", "YYZ1", "--date", "2026-09-03", "--time", "07:00", "--keep-db", "--simulate-crash"))

    print("=== STEP 4: restart after crash — S-004 must NOT be selected again ===")
    print(run_demo("--pref", "YYZ1", "--date", "2026-09-03", "--time", "07:00", "--keep-db"))

    print("=== STEP 5: audit trail (proof of exactly-one-selection) ===")
    import sqlite3
    with sqlite3.connect(DB) as conn:
        for row in conn.execute(
            "SELECT shift_id, action, detail FROM claim_audit ORDER BY id"
        ):
            print("   ", row)

    server.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())