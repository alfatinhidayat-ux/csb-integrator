#!/usr/bin/env python3
"""Pantau migrasi Brighter -> CSB (jalankan LANGSUNG di server).

Cara pakai (login dulu ke server 31.97.67.49 sebagai root):
    cd /home/csb-integrator
    python3 pantau_migrasi.py            # sekali tampil
    python3 pantau_migrasi.py --watch    # auto-refresh tiap 10 detik
    python3 pantau_migrasi.py --watch 5  # tiap 5 detik
    python3 pantau_migrasi.py --db       # hanya query DB aktif
    python3 pantau_migrasi.py --log      # tail -f log migrasi yang sedang berjalan
    python3 pantau_migrasi.py --sync     # cek sync dash_penerimaan_per_user (untuk rekon cb2 Juni)

Tidak butuh argumen tambahan: kredensial DB dibaca dari config.py/.env.
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
PROC_PATTERNS = ("run_migration", "run_sequence", "reconcile", "sync_dash", "refresh", "catchup")

DB_SQL = """
SELECT id, user, host, command, time, LEFT(state, 25) AS st,
       LEFT(REPLACE(info, '\\n', ' '), 70) AS info
FROM information_schema.processlist
WHERE command NOT IN ('Sleep', 'Daemon')
ORDER BY time DESC
"""


def db():
    try:
        sys.path.insert(0, str(ROOT))
        from config import Config  # noqa: F401
    except Exception:
        Config = None

    if Config is not None:
        cfg = Config.from_env()
        kwargs = cfg.csb_db_kwargs()
        import pymysql
        return pymysql.connect(
            host=kwargs["host"], port=kwargs["port"],
            user=kwargs["user"], password=kwargs["password"],
            database=kwargs["database"], charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=8, read_timeout=8,
        )
    return None


def fmt_num(v):
    if v is None:
        return "-"
    try:
        return f"{float(v):,.0f}"
    except Exception:
        return str(v)


def show_processes():
    print("── PROSES MIGRASI BERJALAN ───────────────────────────────────")
    out = subprocess.run(
        ["ps", "aux"], capture_output=True, text=True
    ).stdout
    rows = [l for l in out.splitlines() if any(p in l for p in PROC_PATTERNS)]
    if not rows:
        print("(tidak ada proses migrasi aktif)")
        return
    for l in rows[:12]:
        f = l.split(None, 10)
        if len(f) >= 11:
            print(f"pid={f[1]:>8}  {f[9]:>8}  {' '.join(f[10:])[:110]}")


def show_sequence():
    print("\n── STATUS SEQUENCE (sequence_serial.log) ─────────────────────")
    log = LOG_DIR / "sequence_serial.log"
    if not log.exists():
        print("(belum ada log sequence)")
        return
    lines = log.read_text(errors="ignore").splitlines()[-6:]
    for l in lines:
        print(l)


def show_recent_logs():
    print("\n── LOG AKTIF (terbaru) ───────────────────────────────────────")
    files = sorted(
        [p for p in LOG_DIR.glob("*.log") if p.stat().st_mtime < time.time() + 60],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not files:
        files = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[:8]:
        age = time.time() - p.stat().st_mtime
        if age < 3600:
            print(f"{p.name:<50s} {age:5.0f}s lalu")
        else:
            print(f"{p.name:<50s} {age/3600:5.1f} jam lalu")


def tail_active_log():
    """Tail -f log migrasi yang paling baru berubah (yang sedang ditulis)."""
    files = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    files += sorted(LOG_DIR.glob("*.out"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print("(tidak ada log)")
        return
    target = files[0]
    print(f"Tail -f: {target.name}  (log lain: {', '.join(p.name for p in files[1:4])})")
    subprocess.run(["tail", "-f", str(target)])


def show_db_queries():
    print("\n── QUERY DB AKTIF (diurutkan durasi) ─────────────────────────")
    conn = db()
    if conn is None:
        print("(tidak bisa konek DB — cek config.py/.env)")
        return
    try:
        with conn.cursor() as cur:
            cur.execute(DB_SQL)
            rows = cur.fetchall()
        if not rows:
            print("(tidak ada query aktif)")
            return
        for r in rows:
            print(f"id={r['id']:>6} {r['user']:<8s} {r['host']:<20s} {r['time']:>5}s {str(r['st'] or ''):<25s} {r['info'] or ''}")
    finally:
        conn.close()


def show_sync_status():
    print("\n── SYNC dash_penerimaan_per_user (untuk rekon cb2 Juni) ─────")
    conn = db()
    if conn is None:
        print("(tidak bisa konek DB)")
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT cabang_id, bulan, MIN(synced_at) mn, MAX(synced_at) mx
                   FROM dash_penerimaan_per_user
                   WHERE bulan IN ('2026-05-01','2026-06-01','2026-07-01')
                   GROUP BY cabang_id, bulan ORDER BY cabang_id, bulan"""
            )
            rows = cur.fetchall()
        if not rows:
            print("(kosong)")
            return
        for r in rows:
            print(f"cb{r['cabang_id']} {r['bulan']}  sync: {r['mn']} .. {r['mx']}")
    finally:
        conn.close()


def run_watch(interval):
    try:
        while True:
            subprocess.run(["clear"])
            print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            show_processes()
            show_sequence()
            show_recent_logs()
            show_db_queries()
            print(f"\n(auto-refresh tiap {interval}s — Ctrl+C untuk stop)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstop.")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if arg == "--watch":
        interval = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        run_watch(interval)
    elif arg == "--db":
        show_db_queries()
    elif arg == "--log":
        tail_active_log()
    elif arg == "--sync":
        show_sync_status()
    else:
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        show_processes()
        show_sequence()
        show_recent_logs()
        show_db_queries()
        print("\nGunakan: pantau_migrasi.py --watch [detik] | --db | --log | --sync")


if __name__ == "__main__":
    main()
