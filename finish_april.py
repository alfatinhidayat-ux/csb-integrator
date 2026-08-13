#!/usr/bin/env python3
"""Tuntaskan April: tunggu SP refresh yang masih jalan, lalu jalankan ulang
refresh SP (idempotent) + rekon April di background.

Status -> logs/finish_april.log
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATUS = ROOT / "logs" / "finish_april.log"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def emit(msg: str) -> None:
    line = f"[{now()}] {msg}"
    print(line, flush=True)
    with STATUS.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def sp_still_running() -> int:
    """Cek apakah masih ada query (thread) yang mengeksekusi SP backfill_harian."""
    import pymysql
    from config import Config
    from pymysql.cursors import DictCursor
    cfg = Config.from_env()
    conn = pymysql.connect(**cfg.csb_db_kwargs(), cursorclass=DictCursor, charset="utf8mb4")
    try:
        cur = conn.cursor()
        cur.execute("SHOW FULL PROCESSLIST")
        for r in cur.fetchall():
            info = r.get("Info") or ""
            if r.get("Command") == "Query" and "backfill_harian" in info:
                return r.get("Id")
    finally:
        conn.close()
    return 0


def main() -> int:
    emit("== FINISH APRIL MULAI ==")
    env = dict(os.environ)
    env["PHP_BIN"] = str(ROOT / "bin" / "php-csb.sh")
    py = sys.executable

    while True:
        pid = sp_still_running()
        if not pid:
            break
        emit(f"Tunggu SP refresh (thread {pid}) masih berjalan...")
        time.sleep(30)
    emit("SP refresh lama sudah selesai.")

    cmd_refresh = [
        py, "run_migration_feb_apr.py", "--bulan", "2026-04", "--cabang-ids", "1,2,4,5",
        "--apply", "--skip-migrate", "--skip-normalize", "--skip-lp-mirror", "--skip-reconcile",
    ]
    emit(f"REFRESH SP: {' '.join(cmd_refresh)}")
    rc = subprocess.call(cmd_refresh, cwd=ROOT, env=env)
    emit(f"REFRESH SP rc={rc}")

    cmd_recon = [
        py, "reconcile_brighter_clarify.py",
        "--tanggal-awal", "2026-04-01", "--tanggal-akhir", "2026-04-30",
        "--cabang-ids", "1,2,4,5", "--show-components",
    ]
    emit(f"REKON: {' '.join(cmd_recon)}")
    rc2 = subprocess.call(cmd_recon, cwd=ROOT, env=env)
    emit(f"REKON rc={rc2}")

    emit(f"== FINISH APRIL SELESAI refresh_rc={rc} rekon_rc={rc2} ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())