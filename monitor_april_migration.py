"""Monitor migrasi April 2026 di background.

Menulis status berkala ke logs/monitor_april.log. Berhenti sendiri saat
migrasi selesai (SELESAI) atau berhenti dengan masalah (STOP/FAIL/rc!=0).

Pakai:
    python3 monitor_april_migration.py            # monitor log run_migration_feb_apr_*.log terbaru
    python3 monitor_april_migration.py --log <path> [--pid 995982] [--interval 60]
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def proc_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    try:
        return os.path.exists(f"/proc/{pid}")
    except OSError:
        return False


def detect_state(log: Path, pid: int) -> dict:
    alive = proc_alive(pid) if pid > 0 else True
    state = {"alive": alive, "terminal": False, "ok": False, "message": None, "step": None}

    last_lines = []
    try:
        with open(log, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                last_lines.append(line.rstrip())
    except OSError as exc:
        state["message"] = f"gagal baca log: {exc}"
        return state

    tail = last_lines[-200:]
    for line in tail:
        if re.match(r"^STOP: .+ rc=\d+", line):
            state["terminal"] = True
            state["ok"] = False
            state["message"] = line
        elif re.match(r"^SELESAI: .+ selesai tanpa rc error", line):
            state["terminal"] = True
            state["ok"] = True
            state["message"] = line
        elif "Refresh SP: FAIL" in line:
            state["terminal"] = True
            state["ok"] = False
            state["message"] = line

    m = None
    for line in tail:
        m = re.search(r"\[(\d{2}:\d{2}:\d{2})\] \[(\d+)/(\d+) ([^\]]+)\]", line)
        if m:
            break
    if m:
        state["step"] = f"{m.group(2)}/{m.group(3)} {m.group(4)}"

    if not alive and not state["terminal"]:
        state["terminal"] = True
        state["ok"] = False
        state["message"] = "proses migrasi sudah tidak hidup (mati/ter-kill) tanpa marker SELESAI/STOP"

    return state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=None, help="File log wrapper. Default: log run_migration_feb_apr_*.log paling baru.")
    ap.add_argument("--pid", type=int, default=0, help="PID proses migrasi (0 = hanya cek file log).")
    ap.add_argument("--interval", type=int, default=60, help="Detik antar cek.")
    ap.add_argument("--out", default=None, help="File status. Default: logs/monitor_april.log")
    args = ap.parse_args()

    log_path = Path(args.log) if args.log else None
    if log_path is None:
        cands = sorted(glob.glob(str(ROOT / "logs" / "run_migration_feb_apr_*.log")))
        if not cands:
            print(f"{now()} ERROR: tidak ada log run_migration_feb_apr_*.log", flush=True)
            return 1
        log_path = Path(cands[-1])

    out_path = Path(args.out) if args.out else ROOT / "logs" / "monitor_april.log"

    with out_path.open("a", encoding="utf-8", buffering=1) as out:
        def write(msg: str) -> None:
            print(msg, flush=True)
            out.write(msg + "\n")

        write(f"== MONITOR MULAI {now()} ==")
        write(f"   log     : {log_path}")
        write(f"   pid     : {args.pid or '(tanpa pid)'}")

        while True:
            st = detect_state(log_path, args.pid)
            status = "ALIVE" if st["alive"] else "DEAD"
            step = st["step"] or "?"
            write(f"[{now()}] {status} | step={step} | {st['message'] or 'normal'}")
            if st["terminal"]:
                verdict = "SELESAI OK" if st["ok"] else "TERHENTI DENGAN MASALAH"
                write(f"[{now()}] == {verdict} — {st['message']} ==")
                write("== MONITOR BERHENTI ==")
                return 0 if st["ok"] else 1
            time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())