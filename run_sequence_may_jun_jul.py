"""Jalankan migrasi Mei, Juni, Juli 2026 berurutan di background.

Menunggu migrasi April (PID diberikan) selesai dulu, lalu per bulan:
    python run_migration_feb_apr.py --bulan <X> --cabang-ids 1,2,4,5,7 --apply

Setiap bulan memakai log wrapper terpisah:
    logs/run_migration_2026-05.log
    logs/run_migration_2026-06.log
    logs/run_migration_2026-07.log

Status global ditulis ke logs/sequence_may_jun_jul.log
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_MONTHS = ["2026-05", "2026-06", "2026-07"]
DEFAULT_CABANG = "1,2,4,5,7"
PHP_SHIM = ROOT / "bin" / "php-csb.sh"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    return os.path.exists(f"/proc/{pid}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-pid", type=int, default=0, help="PID proses April yang harus selesai dulu.")
    ap.add_argument("--months", default=",".join(DEFAULT_MONTHS))
    ap.add_argument("--cabang-ids", default=DEFAULT_CABANG)
    ap.add_argument("--out", default=None, help="File status global.")
    args = ap.parse_args()

    months = [m for m in args.months.split(",") if m.strip()]
    status_path = Path(args.out) if args.out else ROOT / "logs" / "sequence_may_jun_jul.log"

    env = dict(os.environ)
    env["PHP_BIN"] = str(PHP_SHIM)

    def emit(msg: str) -> None:
        line = f"[{now()}] {msg}"
        print(line, flush=True)
        with status_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    emit(f"== SEQ MULAI: months={months} cabang={args.cabang_ids} wait_pid={args.wait_pid} ==")

    if args.wait_pid > 0 and alive(args.wait_pid):
        emit(f"Menunggu April (PID {args.wait_pid}) selesai...")
        while alive(args.wait_pid):
            time.sleep(30)
        emit("April sudah selesai.")

    overall = 0
    for month in months:
        log_file = ROOT / "logs" / f"run_migration_{month}.log"
        cmd = [
            sys.executable, "run_migration_feb_apr.py",
            "--bulan", month,
            "--cabang-ids", args.cabang_ids,
            "--log-file", str(log_file),
            "--apply",
        ]
        emit(f"START {month}: {' '.join(cmd)}")
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env)
        rc = proc.wait()
        dur = int(time.time() - t0)
        emit(f"END   {month}: rc={rc} durasi={dur//60}m{dur%60}s log={log_file}")
        if rc != 0:
            emit(f"{month}: GAGAL rc={rc} (tetap lanjut ke bulan berikutnya)")
            overall = 1

    emit(f"== SEQ SELESAI overall_rc={overall} ==")
    return overall


if __name__ == "__main__":
    sys.exit(main())