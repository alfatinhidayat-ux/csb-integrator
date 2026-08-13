"""Jalankan migrasi Brighter -> Clarify untuk Februari-April 2026.

Default hanya dry-run. Tambahkan --apply untuk menulis data.

Contoh:
    python run_migration_feb_apr.py --apply
    python run_migration_feb_apr.py --bulan 2026-02 --apply
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from config import Config
from db import DatabaseManager


ROOT = Path(__file__).resolve().parent
LOG_HANDLE = None


@dataclass(frozen=True)
class MonthJob:
    label: str
    start: str
    end: str


MONTHS = [
    MonthJob("2026-02", "2026-02-01", "2026-02-28"),
    MonthJob("2026-03", "2026-03-01", "2026-03-31"),
    MonthJob("2026-04", "2026-04-01", "2026-04-30"),
    MonthJob("2026-05", "2026-05-01", "2026-05-31"),
    MonthJob("2026-06", "2026-06-01", "2026-06-30"),
    MonthJob("2026-07", "2026-07-01", "2026-07-31"),
]


def emit(message: str = "") -> None:
    print(message, flush=True)
    if LOG_HANDLE:
        LOG_HANDLE.write(message + "\n")


def setup_log(path_value: str | None) -> Path:
    global LOG_HANDLE
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(path_value) if path_value else ROOT / "logs" / f"run_migration_feb_apr_{stamp}.log"
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    LOG_HANDLE = path.open("a", encoding="utf-8", buffering=1)
    return path


def run_step(title: str, cmd: list[str]) -> int:
    emit("")
    emit("=" * 100)
    emit(title)
    emit("CMD: " + " ".join(cmd))
    emit("=" * 100)

    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        emit(line.rstrip())
    return proc.wait()


def refresh_sp_for_month(job: MonthJob, cabang_ids: list[int], dry_run: bool) -> int:
    emit("")
    emit("=" * 100)
    emit(f"REFRESH SP SUMMARY {job.label} cabang={cabang_ids} mode={'DRY-RUN' if dry_run else 'APPLY'}")
    emit("=" * 100)

    if dry_run:
        for cabang_id in cabang_ids:
            emit(f"DRY-RUN: CALL sp_fin_dash_backfill_harian('{job.start}', '{job.end}', <kode cabang {cabang_id}>)")
            emit(f"DRY-RUN: CALL sp_fin_dash_fill_target_period({cabang_id}, '{job.end}')")
        emit(f"DRY-RUN: CALL sp_fin_dash_backfill_snapshot('{job.start}', '{job.start}')")
        return 0

    db = DatabaseManager(Config.from_env(), target_db="csb")
    db.connect()
    try:
        cur = db.conn.cursor()
        cur.execute(
            "SELECT id, kode FROM cabang WHERE id IN (%s) AND aktif = 1 ORDER BY id"
            % ",".join(["%s"] * len(cabang_ids)),
            tuple(cabang_ids),
        )
        rows = cur.fetchall()
        kode_by_id = {int(row["id"]): row["kode"] for row in rows}

        for cabang_id in cabang_ids:
            kode = kode_by_id.get(cabang_id)
            if not kode:
                emit(f"SKIP: cabang {cabang_id} tidak aktif/tidak ditemukan")
                continue

            emit(f"- cabang {cabang_id} {kode}: harian")
            cur.execute("CALL sp_fin_dash_backfill_harian(%s, %s, %s)", (job.start, job.end, kode))
            while cur.nextset():
                pass

            emit(f"- cabang {cabang_id} {kode}: target")
            cur.execute("CALL sp_fin_dash_fill_target_period(%s, %s)", (cabang_id, job.end))
            while cur.nextset():
                pass

        emit("- snapshot bulanan/top/akun")
        cur.execute("CALL sp_fin_dash_backfill_snapshot(%s, %s)", (job.start, job.start))
        while cur.nextset():
            pass

        db.conn.commit()
        emit("Refresh SP: OK")
        return 0
    except Exception as exc:
        db.conn.rollback()
        emit(f"Refresh SP: FAIL {exc}")
        return 1
    finally:
        db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wrapper migrasi Februari-April 2026.")
    parser.add_argument("--apply", action="store_true", help="Benar-benar menulis data. Tanpa ini hanya dry-run.")
    parser.add_argument("--cabang-ids", default="1,2,4,5", help="Default: 1,2,4,5. Cabang 7 belum aktif Feb-Apr.")
    parser.add_argument("--bulan", default=None, help="Opsional: hanya satu bulan, contoh 2026-02.")
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--skip-migrate", action="store_true")
    parser.add_argument("--skip-normalize", action="store_true")
    parser.add_argument("--skip-lp-mirror", action="store_true")
    parser.add_argument("--skip-refresh-sp", action="store_true")
    parser.add_argument("--skip-reconcile", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = [m for m in MONTHS if not args.bulan or m.label == args.bulan]
    if not selected:
        emit(f"Bulan tidak dikenal: {args.bulan}. Pilih: {', '.join(m.label for m in MONTHS)}")
        return 2

    cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]
    cabang_arg = ",".join(map(str, cabang_ids))
    py = sys.executable
    log_path = setup_log(args.log_file)

    emit("RUN MIGRASI FEBRUARI-APRIL 2026")
    emit(f"Mode   : {'APPLY' if args.apply else 'DRY-RUN'}")
    emit(f"Cabang : {cabang_arg}")
    emit(f"Bulan  : {', '.join(m.label for m in selected)}")
    emit(f"Log    : {log_path}")

    for idx, job in enumerate(selected, start=1):
        emit("")
        emit("#" * 100)
        emit(f"[{idx}/{len(selected)}] BULAN {job.label} ({job.start}..{job.end})")
        emit("#" * 100)

        steps: list[tuple[str, list[str]]] = []
        if not args.skip_migrate:
            steps.append((
                f"MIGRASI {job.label}",
                [py, "migrate_brighter_to_clarify.py", "--tanggal-awal", job.start,
                 "--tanggal-akhir", job.end, "--cabang-ids", cabang_arg] + (["--apply"] if args.apply else []),
            ))
        if not args.skip_normalize:
            steps.append((
                f"NORMALIZE {job.label}",
                [py, "normalize_clarify_migration.py", "--tanggal-awal", job.start,
                 "--tanggal-akhir", job.end, "--cabang-ids", cabang_arg] + (["--apply"] if args.apply else []),
            ))
        if not args.skip_lp_mirror:
            steps.append((
                f"MIRROR LP PIUTANG {job.label}",
                [py, "reconcile_piutang_pelunasan_headers.py", "--tanggal-awal", job.start,
                 "--tanggal-akhir", job.end, "--cabang-ids", cabang_arg] + (["--apply"] if args.apply else []),
            ))

        for title, cmd in steps:
            rc = run_step(title, cmd)
            if rc != 0:
                emit(f"STOP: {title} gagal rc={rc}")
                return rc

        if not args.skip_refresh_sp:
            rc = refresh_sp_for_month(job, cabang_ids, dry_run=not args.apply)
            if rc != 0:
                emit(f"STOP: refresh SP {job.label} gagal rc={rc}")
                return rc

        if not args.skip_reconcile:
            rc = run_step(
                f"REKONSILIASI {job.label}",
                [py, "reconcile_brighter_clarify.py", "--tanggal-awal", job.start,
                 "--tanggal-akhir", job.end, "--cabang-ids", cabang_arg, "--show-components"],
            )
            if rc != 0:
                emit(f"STOP: rekonsiliasi {job.label} masih perlu perhatian rc={rc}")
                return rc

    emit("")
    emit("=" * 100)
    emit("SELESAI: semua bulan yang dipilih selesai tanpa rc error.")
    emit("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
