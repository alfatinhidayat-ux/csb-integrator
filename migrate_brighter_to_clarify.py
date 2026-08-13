"""Entrypoint aman migrasi Brighter -> Clarify per bulan/cabang.

Default script ini hanya menampilkan rencana. Tambahkan --apply untuk benar-benar
menjalankan sinkronisasi/upsert dan landing ke tabel Clarify.

Catatan desain:
- Tidak memanggil runner.py clean_start() karena berisiko truncate banyak tabel.
- Cabang 7/Piru otomatis dilewati sebelum 2026-05-01.
- Pembelian dan pelunasan hutang supplier tetap ditarik sebagai acuan Brighter,
  tetapi tidak dipaksa posting ke KasBank.
"""

from __future__ import annotations

import argparse
import calendar
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pymysql

from config import Config
from db import DatabaseManager
from sync_saldo_kas_harian import load_cabang_urls


ROOT = Path(__file__).resolve().parent
BACKEND_DIR = (ROOT / ".." / "csb-backend-api").resolve()
ACTIVE_SINCE = {7: date(2026, 5, 1)}
LOG_HANDLE = None


@dataclass(frozen=True)
class MonthWindow:
    label: str
    start: date
    end: date


def default_log_path(prefix: str, start: date, end: date) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "logs" / f"{prefix}_{start}_{end}_{stamp}.log"


def setup_log(path_value: str | None, prefix: str, start: date, end: date) -> Path:
    global LOG_HANDLE
    path = Path(path_value) if path_value else default_log_path(prefix, start, end)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    LOG_HANDLE = path.open("a", encoding="utf-8", buffering=1)
    return path


def emit(message: str = "") -> None:
    print(message, flush=True)
    if LOG_HANDLE:
        LOG_HANDLE.write(message + "\n")


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def iter_months(start: date, end: date) -> list[MonthWindow]:
    cur = start.replace(day=1)
    out: list[MonthWindow] = []
    while cur <= end:
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        win_start = max(start, cur)
        win_end = min(end, last)
        out.append(MonthWindow(cur.strftime("%Y-%m"), win_start, win_end))
        cur = (cur + timedelta(days=32)).replace(day=1)
    return out


def php_bin() -> str:
    env = os.environ.get("PHP_BIN")
    if env:
        return env
    laragon = Path(r"D:\laragon\bin\php\php-8.3.26-Win32-vs16-x64\php.exe")
    return str(laragon) if laragon.exists() else "php"


def run(
    title: str,
    cmd: list[str],
    *,
    cwd: Path = ROOT,
    apply: bool,
    index: int | None = None,
    total: int | None = None,
) -> int:
    prefix = f"{index}/{total} " if index is not None and total is not None else ""
    emit(f"\n[{datetime.now():%H:%M:%S}] [{prefix}{title}]")
    emit("  $ " + " ".join(cmd))
    if not apply:
        emit("  status: SKIP eksekusi (dry-run rencana)")
        return 0
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        emit(line.rstrip("\n"))
    rc = proc.wait()
    emit(f"  status: {'OK' if rc == 0 else 'FAIL'} rc={rc}")
    return rc


def active_cabangs_for_window(cabang_ids: list[int], window: MonthWindow) -> list[int]:
    active = []
    for cid in cabang_ids:
        since = ACTIVE_SINCE.get(cid)
        if since and window.end < since:
            continue
        active.append(cid)
    return active


def active_cabangs_for_months(cabang_ids: list[int], months: list[MonthWindow]) -> list[int]:
    active: set[int] = set()
    for window in months:
        active.update(active_cabangs_for_window(cabang_ids, window))
    return sorted(active)


def date_range(start: date, end: date) -> list[date]:
    out = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def load_cabang_ids(arg_value: str | None) -> list[int]:
    cfg = Config.from_env()
    db = DatabaseManager(cfg, target_db="csb")
    db.connect()
    try:
        urls = load_cabang_urls(db)
    finally:
        db.close()
    if arg_value:
        return [int(x.strip()) for x in arg_value.split(",") if x.strip()]
    return sorted(urls)


def upsert_bulanan_dashboard_from_dash(
    tanggal_awal: date,
    tanggal_akhir: date,
    cabang_ids: list[int],
    apply: bool,
    index: int | None = None,
    total: int | None = None,
) -> None:
    """Isi/refresh brighter_rekap_bulanan_dashboard dari dash_* + rekap harian.

    Ini acuan audit akhir per bulan. Penjualan = total_rp + total_piutang_rp
    dari dash_rekap_dashboard jenis penjualan; retur = total_rp retur_jual;
    cash_in = jumlah metode cash dari brighter_penerimaan_rekap.
    """
    title = "Upsert brighter_rekap_bulanan_dashboard dari dash_*"
    active_by_month = {
        window.label: active_cabangs_for_window(cabang_ids, window)
        for window in iter_months(tanggal_awal, tanggal_akhir)
    }
    prefix = f"{index}/{total} " if index is not None and total is not None else ""
    emit(f"\n[{datetime.now():%H:%M:%S}] [{prefix}{title}]")
    emit(f"  range={tanggal_awal}..{tanggal_akhir} cabang_aktif={active_by_month}")
    if not apply:
        emit("  status: SKIP eksekusi (dry-run rencana)")
        return

    cfg = Config.from_env()
    kw = cfg.csb_db_kwargs()
    conn = pymysql.connect(**kw, cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4", autocommit=False)
    try:
        cur = conn.cursor()
        for window in iter_months(tanggal_awal, tanggal_akhir):
            active = active_cabangs_for_window(cabang_ids, window)
            for cid in active:
                cur.execute(
                    """
                    SELECT
                        COALESCE(SUM(CASE WHEN jenis_dashboard = 'penjualan'
                            THEN COALESCE(JSON_EXTRACT(rekap_json, '$.total_rp') + 0, 0)
                               + COALESCE(JSON_EXTRACT(rekap_json, '$.total_piutang_rp') + 0, 0)
                            ELSE 0 END), 0) AS penjualan,
                        COALESCE(SUM(CASE WHEN jenis_dashboard = 'retur_jual'
                            THEN COALESCE(JSON_EXTRACT(rekap_json, '$.total_rp') + 0, 0) ELSE 0 END), 0) AS retur_jual
                    FROM dash_rekap_dashboard
                    WHERE cabang_id = %s AND bulan = %s
                    """,
                    (cid, window.start.replace(day=1).isoformat()),
                )
                dash = cur.fetchone() or {}
                penjualan = float(dash.get("penjualan") or 0)
                retur = float(dash.get("retur_jual") or 0)
                cur.execute(
                    """
                    SELECT COALESCE(SUM(tunai + transfer + card + qris + wallet), 0) AS cash_in
                    FROM brighter_penerimaan_rekap
                    WHERE cabang_id = %s AND tanggal BETWEEN %s AND %s
                    """,
                    (cid, window.start.isoformat(), window.end.isoformat()),
                )
                cash = float((cur.fetchone() or {}).get("cash_in") or 0)
                emit(
                    f"  upsert cb{cid} {window.label}: "
                    f"penjualan={penjualan:,.0f} retur={retur:,.0f} cash_in={cash:,.0f}"
                )
                cur.execute(
                    """
                    INSERT INTO brighter_rekap_bulanan_dashboard
                        (cabang_id, bulan, penjualan, retur_jual, penjualan_bersih, cash_in, keterangan, aktif)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
                    ON DUPLICATE KEY UPDATE
                        penjualan = VALUES(penjualan),
                        retur_jual = VALUES(retur_jual),
                        penjualan_bersih = VALUES(penjualan_bersih),
                        cash_in = VALUES(cash_in),
                        keterangan = VALUES(keterangan),
                        aktif = 1,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        cid,
                        window.start.replace(day=1).isoformat(),
                        penjualan,
                        retur,
                        penjualan - retur,
                        cash,
                        "AUTO dari dash_rekap_dashboard + brighter_penerimaan_rekap",
                    ),
                )
        conn.commit()
        emit("  status: OK")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrasi Brighter -> Clarify terorkestrasi dan aman")
    parser.add_argument("--tanggal-awal", default="2026-01-01")
    parser.add_argument("--tanggal-akhir", default=date.today().isoformat())
    parser.add_argument("--cabang-ids", default=None)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--apply", action="store_true", help="Jalankan perubahan. Tanpa flag ini hanya rencana.")
    parser.add_argument("--skip-pos", action="store_true")
    parser.add_argument("--skip-retur", action="store_true")
    parser.add_argument("--skip-piutang", action="store_true")
    parser.add_argument("--skip-kasbank", action="store_true")
    parser.add_argument("--skip-finance", action="store_true")
    parser.add_argument("--skip-landing", action="store_true")
    parser.add_argument("--log-file", default=None, help="File log progres. Default: logs/migrate_*.log")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    start = parse_date(args.tanggal_awal)
    end = parse_date(args.tanggal_akhir)
    if end < start:
        raise SystemExit("tanggal-akhir lebih kecil dari tanggal-awal")

    log_path = setup_log(args.log_file, "migrate", start, end)
    cabang_ids = load_cabang_ids(args.cabang_ids)
    months = iter_months(start, end)
    emit("=" * 80)
    emit("MIGRASI BRIGHTER -> CLARIFY")
    emit(f"Mode    : {'APPLY' if args.apply else 'DRY-RUN'}")
    emit(f"Range   : {start}..{end}")
    emit(f"Cabang  : {cabang_ids}")
    emit(f"Bulan   : {[m.label for m in months]}")
    emit(f"Log     : {log_path}")
    emit("=" * 80)

    py = sys.executable
    failures: list[tuple[str, int]] = []
    active_for_range = active_cabangs_for_months(cabang_ids, months)
    total_steps = len(months) * (2 + (0 if args.skip_pos else 1) + (0 if args.skip_retur else 1) + (0 if args.skip_kasbank else 1))
    total_steps += 0 if args.skip_piutang else 1
    total_steps += 0 if args.skip_finance else 1
    total_steps += 1
    if not args.skip_landing:
        landing_days = sum(len(active_cabangs_for_window(cabang_ids, w)) * len(date_range(w.start, w.end)) for w in months)
        total_steps += 0 if args.skip_pos else landing_days
        total_steps += 0 if args.skip_retur else 1
        total_steps += 0 if args.skip_piutang else 2
    step_no = 0

    def next_step() -> int:
        nonlocal step_no
        step_no += 1
        return step_no

    for window in months:
        active = active_cabangs_for_window(cabang_ids, window)
        if not active:
            emit(f"\n[{window.label}] SKIP: tidak ada cabang aktif")
            continue
        cabang_arg = ",".join(map(str, active))

        rc = run(
            f"{window.label} dash_* dan rekap dashboard",
            [py, "sync_dash_api.py", "--env", "--bulan", window.label, "--cabang-ids", cabang_arg]
            + (["--verbose"] if args.verbose else [])
            + ([] if args.apply else ["--dry-run"]),
            apply=True,
            index=next_step(),
            total=total_steps,
        )
        if rc:
            failures.append((f"dash {window.label}", rc))

        if not args.skip_pos:
            rc = run(
                f"{window.label} POS staging brighter_pos",
                [py, "sync_pos_rekap.py", "--env", "--tanggal-awal", window.start.isoformat(),
                 "--tanggal-akhir", window.end.isoformat(), "--cabang-ids", cabang_arg,
                 "--workers", str(args.workers), "--force"],
                apply=args.apply,
                index=next_step(),
                total=total_steps,
            )
            if rc:
                failures.append((f"pos {window.label}", rc))

        if not args.skip_retur:
            rc = run(
                f"{window.label} Retur staging",
                [py, "sync_retur_penjualan.py", "--env", "--tanggal-awal", window.start.isoformat(),
                 "--tanggal-akhir", window.end.isoformat(), "--cabang-ids", cabang_arg,
                 "--workers", str(args.workers)],
                apply=args.apply,
                index=next_step(),
                total=total_steps,
            )
            if rc:
                failures.append((f"retur {window.label}", rc))

        rc = run(
            f"{window.label} Rekap penerimaan harian",
            [py, "sync_saldo_kas_harian.py", "--env", "--tanggal-awal", window.start.isoformat(),
             "--tanggal-akhir", window.end.isoformat(), "--cabang-ids", cabang_arg],
            apply=args.apply,
            index=next_step(),
            total=total_steps,
        )
        if rc:
            failures.append((f"rekap {window.label}", rc))

        if not args.skip_kasbank:
            rc = run(
                f"{window.label} KasBank -> Clarify",
                [py, "backfill_kasbank_all.py", "--env", "--bulan", window.label,
                 "--cabang-ids", cabang_arg] + (["--apply"] if args.apply else []),
                apply=True,
                index=next_step(),
                total=total_steps,
            )
            if rc:
                failures.append((f"kasbank {window.label}", rc))

    if not args.skip_piutang:
        rc = run(
            "Pelunasan piutang staging",
            [py, "sync_pelunasan.py", "--env", "--cabang-ids", ",".join(map(str, active_for_range))]
            + (["--verbose"] if args.verbose else []),
            apply=args.apply,
            index=next_step(),
            total=total_steps,
        )
        if rc:
            failures.append(("sync_pelunasan", rc))

    if not args.skip_finance:
        rc = run(
            "Finance Brighter pembelian/hutang/piutang",
            [py, "sync_finance.py", "--env", "--cabang-ids", ",".join(map(str, active_for_range)),
             "--workers", str(args.workers)] + (["--verbose"] if args.verbose else []),
            apply=args.apply,
            index=next_step(),
            total=total_steps,
        )
        if rc:
            failures.append(("sync_finance", rc))

    upsert_bulanan_dashboard_from_dash(start, end, cabang_ids, args.apply, next_step(), total_steps)

    if not args.skip_landing:
        php = php_bin()
        if not args.skip_pos:
            for window in months:
                active = active_cabangs_for_window(cabang_ids, window)
                for cid in active:
                    for day in date_range(window.start, window.end):
                        rc = run(
                            f"Landing POS cb{cid} {day}",
                            [php, "artisan", "pos:reconcile-missing", "--cabang", str(cid),
                             "--date", day.isoformat(), "--refresh"] + ([] if args.apply else ["--dry-run"]),
                            cwd=BACKEND_DIR,
                            apply=True,
                            index=next_step(),
                            total=total_steps,
                        )
                        if rc:
                            failures.append((f"landing pos cb{cid} {day}", rc))

        if not args.skip_retur:
            rc = run(
                "Landing Retur -> Clarify",
                [py, "migrate_retur_to_app.py"] + (["--yes"] if args.apply else ["--dry-run"]),
                apply=True,
                index=next_step(),
                total=total_steps,
            )
            if rc:
                failures.append(("landing retur", rc))

        if not args.skip_piutang:
            rc = run(
                "Landing Piutang -> Clarify",
                [php, "artisan", "migrate:legacy-piutang"] + ([] if args.apply else ["--dry-run"]),
                cwd=BACKEND_DIR,
                apply=True,
                index=next_step(),
                total=total_steps,
            )
            if rc:
                failures.append(("landing piutang", rc))
            rc = run(
                "Landing Pelunasan Piutang -> Clarify",
                [php, "artisan", "backfill:piutang-pelunasan"] + ([] if args.apply else ["--dry-run"]),
                cwd=BACKEND_DIR,
                apply=True,
                index=next_step(),
                total=total_steps,
            )
            if rc:
                failures.append(("landing pelunasan piutang", rc))

    emit("\n" + "=" * 80)
    if failures:
        emit("SELESAI DENGAN PERINGATAN")
        for name, rc in failures:
            emit(f"  {name}: rc={rc}")
        return 1
    emit("SELESAI OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
