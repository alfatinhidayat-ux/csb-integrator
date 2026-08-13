#!/usr/bin/env python3
"""Serial runner: tiap bulan dijalankan berurutan DAN wajib di-finish
(mirror LP + refresh SP + rekon) sebelum lanjut ke bulan berikutnya.
Menghindari eksekusi 2 bulan bersamaan (lock contention pos:reckon-headers).

Status -> logs/sequence_serial.log
"""
from __future__ import annotations

import calendar
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATUS = ROOT / "logs" / "sequence_serial.log"
CABANG = "1,2,4,5,7"
MONTHS = ["2026-05", "2026-06", "2026-07"]

env = dict(os.environ)
env["PHP_BIN"] = str(ROOT / "bin" / "php-csb.sh")
py = sys.executable


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def emit(msg: str) -> None:
    line = f"[{now()}] {msg}"
    print(line, flush=True)
    with STATUS.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def wrapper_running(month: str) -> bool:
    out = subprocess.run(["ps", "-ef"], capture_output=True, text=True).stdout
    return f"run_migration_feb_apr.py --bulan {month}" in out


def wait_wrapper(month: str) -> None:
    emit(f"Menunggu wrapper {month} selesai...")
    while wrapper_running(month):
        time.sleep(30)
    emit(f"Wrapper {month} sudah selesai.")


def run(cmd, label: str) -> int:
    emit(f"{label}: {' '.join(cmd)}")
    rc = subprocess.call(cmd, cwd=ROOT, env=env)
    emit(f"{label} rc={rc}")
    return rc


def finish_month(month: str) -> None:
    y, m = int(month[:4]), int(month[5:7])
    start = f"{month}-01"
    end = f"{month}-{calendar.monthrange(y, m)[1]:02d}"
    emit(f"== FINISH {month} ==")
    run([
        py, "reconcile_piutang_pelunasan_headers.py",
        "--tanggal-awal", start, "--tanggal-akhir", end,
        "--cabang-ids", CABANG, "--apply",
    ], f"MIRROR LP {month}")
    run([
        py, "run_migration_feb_apr.py", "--bulan", month,
        "--cabang-ids", CABANG, "--apply",
        "--skip-migrate", "--skip-normalize", "--skip-lp-mirror", "--skip-reconcile",
    ], f"REFRESH SP {month}")
    run([
        py, "reconcile_brighter_clarify.py",
        "--tanggal-awal", start, "--tanggal-akhir", end,
        "--cabang-ids", CABANG, "--show-components",
    ], f"REKON {month}")
    emit(f"== FINISH {month} SELESAI ==")


def main() -> int:
    emit("== SERIAL RUNNER MULAI (5->6->7, tiap bulan wajib finish) ==")
    for i, month in enumerate(MONTHS):
        if i == 0:
            wait_wrapper(month)
        else:
            log = ROOT / "logs" / f"run_migration_{month}.log"
            cmd = [
                py, "run_migration_feb_apr.py", "--bulan", month,
                "--cabang-ids", CABANG, "--log-file", str(log), "--apply",
            ]
            run(cmd, f"WRAPPER {month}")
        finish_month(month)
    emit("== SERIAL RUNNER SELESAI ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())