"""Rekonsiliasi Brighter dashboard vs Clarify.

Read-only secara default. Dengan --fix, script menjalankan ulang migrasi scoped
untuk cabang/bulan yang bermasalah, bukan rerun semua.
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
ACTIVE_SINCE = {7: date(2026, 5, 1)}
LOG_HANDLE = None

KAS_JENIS_PLUS = (
    "kas_masuk",
    "kas_masuk_penerimaan_lain",
    "kas_masuk_pelunasan_piutang_karyawan",
    "deposit_pelanggan",
)
KAS_JENIS_MINUS = (
    "kas_keluar",
    "kas_keluar_pengeluaran_lain",
    "kas_keluar_pinjaman_karyawan",
    "pelunasan_hutang_pembelian",
)


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
        out.append(MonthWindow(cur.strftime("%Y-%m"), max(start, cur), min(end, last)))
        cur = (cur + timedelta(days=32)).replace(day=1)
    return out


def active_cabangs_for_window(cabang_ids: list[int], window: MonthWindow) -> list[int]:
    out = []
    for cid in cabang_ids:
        since = ACTIVE_SINCE.get(cid)
        if since and window.end < since:
            continue
        out.append(cid)
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


def one(cur, sql: str, params: tuple) -> float:
    cur.execute(sql, params)
    row = cur.fetchone() or {}
    return float(row.get("v") or 0)


def one_int(cur, sql: str, params: tuple) -> int:
    cur.execute(sql, params)
    row = cur.fetchone() or {}
    return int(row.get("n") or 0)


def audit_month(cur, cid: int, window: MonthWindow) -> dict:
    month = window.start.replace(day=1).isoformat()
    start = window.start.isoformat()
    end = window.end.isoformat()

    ref_cash = one(
        cur,
        """
        SELECT COALESCE(SUM(tunai + transfer + card + qris + wallet), 0) AS v
        FROM brighter_penerimaan_rekap
        WHERE cabang_id = %s AND tanggal BETWEEN %s AND %s
        """,
        (cid, start, end),
    )

    api_user = one(
        cur,
        """
        SELECT COALESCE(SUM(
            CASE
                WHEN jenis_dashboard = 'penjualan'
                    THEN JSON_EXTRACT(rekap_json, '$.total_rp') + 0
                WHEN jenis_dashboard = 'pelunasan_piutang_penjualan'
                    THEN JSON_EXTRACT(rekap_json, '$.fpiutang_bayar') + 0
                WHEN jenis_dashboard IN ('kas_masuk', 'kas_masuk_penerimaan_lain', 'kas_masuk_pelunasan_piutang_karyawan')
                    THEN JSON_EXTRACT(rekap_json, '$.nominal') + 0
                WHEN jenis_dashboard = 'deposit_pelanggan'
                    THEN (
                        COALESCE(JSON_EXTRACT(rekap_json, '$.tunai_rp') + 0, JSON_EXTRACT(rekap_json, '$.total_tunai_rp') + 0, 0)
                        + COALESCE(JSON_EXTRACT(rekap_json, '$.transfer_rp') + 0, JSON_EXTRACT(rekap_json, '$.total_transfer_rp') + 0, 0)
                        + COALESCE(JSON_EXTRACT(rekap_json, '$.card_rp') + 0, JSON_EXTRACT(rekap_json, '$.total_card_rp') + 0, 0)
                        + COALESCE(JSON_EXTRACT(rekap_json, '$.qris_rp') + 0, 0)
                        + COALESCE(JSON_EXTRACT(rekap_json, '$.total_qris_barcode_rp') + 0, 0)
                        + COALESCE(JSON_EXTRACT(rekap_json, '$.total_qris_scan_rp') + 0, 0)
                        + COALESCE(JSON_EXTRACT(rekap_json, '$.deposit_rp') + 0, JSON_EXTRACT(rekap_json, '$.total_wallet_rp') + 0, 0)
                    )
                WHEN jenis_dashboard = 'retur_jual'
                    THEN -(JSON_EXTRACT(rekap_json, '$.rjproduk_total_rp') + 0)
                WHEN jenis_dashboard IN ('kas_keluar', 'kas_keluar_pengeluaran_lain', 'kas_keluar_pinjaman_karyawan', 'kas_keluar_pengeluaran_gaji_karyawan', 'pelunasan_hutang_pembelian')
                    THEN -(JSON_EXTRACT(rekap_json, '$.nominal') + 0)
                ELSE 0
            END
        ), 0) AS v
        FROM dash_penerimaan_per_user
        WHERE cabang_id = %s AND bulan = %s
        """,
        (cid, month),
    )

    pos = one(
        cur,
        """
        SELECT COALESCE(SUM(
            COALESCE(d.tunai_rp, 0)
          + COALESCE(d.transfer_rp, 0)
          + COALESCE(d.card_rp, 0)
          + COALESCE(d.qris_barcode_rp, 0)
          + COALESCE(d.qris_scan_rp, 0)
          + COALESCE(d.wallet_rp, 0)
        ), 0) AS v
        FROM dash_detail_penjualan d
        JOIN pos_transactions p
          ON p.cabang_id = d.cabang_id
         AND p.invoice_number COLLATE utf8mb4_unicode_ci = d.jproduk_nobukti COLLATE utf8mb4_unicode_ci
        WHERE d.cabang_id = %s
          AND d.bulan = %s
        """,
        (cid, month),
    )
    pelunasan_piutang = one(
        cur,
        """
        SELECT COALESCE(SUM(total_pelunasan), 0) AS v
        FROM piutang_pelunasan
        WHERE cabang_id = %s
          AND tanggal BETWEEN %s AND %s
          AND status = 'posted'
        """,
        (cid, start, end),
    )
    retur = one(
        cur,
        """
        SELECT COALESCE(SUM(total_retur), 0) AS v
        FROM retur_penjualan
        WHERE cabang_id = %s
          AND tanggal BETWEEN %s AND %s
          AND status = 'posted'
          AND deleted_at IS NULL
        """,
        (cid, start, end),
    )
    kas_masuk = one(
        cur,
        """
        SELECT COALESCE(SUM(total_nominal), 0) AS v
        FROM kas_bank
        WHERE cabang_id = %s AND tanggal BETWEEN %s AND %s
          AND tipe = 'masuk' AND status = 'approved'
        """,
        (cid, start, end),
    )
    kas_keluar = one(
        cur,
        """
        SELECT COALESCE(SUM(total_nominal), 0) AS v
        FROM kas_bank
        WHERE cabang_id = %s AND tanggal BETWEEN %s AND %s
          AND tipe = 'keluar' AND status = 'approved'
        """,
        (cid, start, end),
    )
    hutang_supplier = one(
        cur,
        """
        SELECT COALESCE(SUM(JSON_EXTRACT(rekap_json, '$.nominal') + 0), 0) AS v
        FROM dash_penerimaan_per_user
        WHERE cabang_id = %s AND bulan = %s
          AND jenis_dashboard = 'pelunasan_hutang_pembelian'
        """,
        (cid, month),
    )
    deposit = one(
        cur,
        """
        SELECT COALESCE(SUM(COALESCE(jumlah_rp, 0)), 0) AS v
        FROM dash_detail_deposit_pelanggan
        WHERE cabang_id = %s AND bulan = %s
        """,
        (cid, month),
    )
    system_cash = pos + pelunasan_piutang + kas_masuk + deposit - retur - kas_keluar - hutang_supplier

    missing_pos = one_int(
        cur,
        """
        SELECT COUNT(*) AS n
        FROM dash_detail_penjualan d
        LEFT JOIN pos_transactions p
          ON p.cabang_id = d.cabang_id
         AND p.invoice_number COLLATE utf8mb4_unicode_ci = d.jproduk_nobukti COLLATE utf8mb4_unicode_ci
        WHERE d.cabang_id = %s AND d.bulan = %s AND p.id IS NULL
        """,
        (cid, month),
    )
    missing_retur = one_int(
        cur,
        """
        SELECT COUNT(*) AS n
        FROM dash_detail_retur_penjualan d
        LEFT JOIN retur_penjualan r
          ON r.cabang_id = d.cabang_id
         AND r.no_retur COLLATE utf8mb4_unicode_ci = d.no_bukti COLLATE utf8mb4_unicode_ci
        WHERE d.cabang_id = %s AND d.bulan = %s AND r.id IS NULL
        """,
        (cid, month),
    )
    missing_piutang = one_int(
        cur,
        """
        SELECT COUNT(*) AS n
        FROM dash_detail_piutang d
        LEFT JOIN piutang_pelunasan p
          ON p.cabang_id = d.cabang_id
         AND p.pelunasan_number COLLATE utf8mb4_unicode_ci = d.fpiutang_nobukti COLLATE utf8mb4_unicode_ci
        WHERE d.cabang_id = %s AND d.bulan = %s AND p.id IS NULL
        """,
        (cid, month),
    )

    return {
        "cabang_id": cid,
        "bulan": window.label,
        "ref_cash": ref_cash,
        "api_user": api_user,
        "system_cash": system_cash,
        "diff_ref_vs_api_user": api_user - ref_cash,
        "diff_system_vs_ref": system_cash - ref_cash,
        "missing_pos": missing_pos,
        "missing_retur": missing_retur,
        "missing_piutang": missing_piutang,
        "components": {
            "pos": pos,
            "pelunasan_piutang": pelunasan_piutang,
            "retur": retur,
            "kas_masuk": kas_masuk,
            "kas_keluar": kas_keluar,
            "pelunasan_hutang_supplier": hutang_supplier,
            "deposit_pelanggan": deposit,
        },
    }


def classify(row: dict, tolerance: float) -> list[str]:
    reasons = []
    if abs(row["diff_ref_vs_api_user"]) > tolerance:
        reasons.append("dash_penerimaan_per_user tidak sama dengan brighter_penerimaan_rekap")
    if abs(row["diff_system_vs_ref"]) > tolerance:
        reasons.append("Clarify cash-in belum sama dengan rekap Brighter")
    if row["missing_pos"]:
        reasons.append(f"{row['missing_pos']} faktur POS belum landing ke pos_transactions")
    if row["missing_retur"]:
        reasons.append(f"{row['missing_retur']} retur belum landing ke retur_penjualan")
    if row["missing_piutang"]:
        reasons.append(f"{row['missing_piutang']} pelunasan piutang belum landing ke piutang_pelunasan")
    return reasons


def run_fix(row: dict, apply: bool) -> int:
    month = row["bulan"]
    start = f"{month}-01"
    y, m = [int(x) for x in month.split("-")]
    end = f"{month}-{calendar.monthrange(y, m)[1]:02d}"
    cid = str(row["cabang_id"])
    cmd = [
        sys.executable,
        "migrate_brighter_to_clarify.py",
        "--tanggal-awal",
        start,
        "--tanggal-akhir",
        end,
        "--cabang-ids",
        cid,
    ]
    if apply:
        cmd.append("--apply")
    emit("  fix $ " + " ".join(cmd))
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
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
    return proc.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit/rekonsiliasi Brighter vs Clarify")
    parser.add_argument("--tanggal-awal", default="2026-01-01")
    parser.add_argument("--tanggal-akhir", default=date.today().isoformat())
    parser.add_argument("--cabang-ids", default=None)
    parser.add_argument("--tolerance", type=float, default=0.99)
    parser.add_argument("--fix", action="store_true", help="Jalankan migrasi scoped untuk bulan/cabang bermasalah")
    parser.add_argument("--apply", action="store_true", help="Bersama --fix, benar-benar menulis perubahan")
    parser.add_argument("--show-components", action="store_true")
    parser.add_argument("--log-file", default=None, help="File log progres. Default: logs/reconcile_*.log")
    args = parser.parse_args()

    start = parse_date(args.tanggal_awal)
    end = parse_date(args.tanggal_akhir)
    log_path = setup_log(args.log_file, "reconcile", start, end)
    cabang_ids = load_cabang_ids(args.cabang_ids)
    months = iter_months(start, end)
    total_checks = sum(len(active_cabangs_for_window(cabang_ids, window)) for window in months)
    check_no = 0

    cfg = Config.from_env()
    kw = cfg.csb_db_kwargs()
    conn = pymysql.connect(**kw, cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4")
    bad_rows: list[tuple[dict, list[str]]] = []
    try:
        cur = conn.cursor()
        emit("=" * 110)
        emit(f"REKONSILIASI BRIGHTER VS CLARIFY {start}..{end} cabang={cabang_ids}")
        emit(f"Log: {log_path}")
        emit("=" * 110)
        for window in months:
            for cid in active_cabangs_for_window(cabang_ids, window):
                check_no += 1
                row = audit_month(cur, cid, window)
                reasons = classify(row, args.tolerance)
                status = "OK" if not reasons else "DIFF"
                emit(
                    f"[{check_no}/{total_checks}] cb{cid:<2} {window.label} ref={row['ref_cash']:>15,.0f} "
                    f"api_user={row['api_user']:>15,.0f} system={row['system_cash']:>15,.0f} "
                    f"d_sys={row['diff_system_vs_ref']:>+14,.0f} "
                    f"miss(pos/ret/piu)={row['missing_pos']}/{row['missing_retur']}/{row['missing_piutang']} [{status}]"
                )
                if args.show_components:
                    c = row["components"]
                    emit(
                        "     "
                        f"pos={c['pos']:,.0f} pel_piut={c['pelunasan_piutang']:,.0f} "
                        f"kas_masuk={c['kas_masuk']:,.0f} deposit={c['deposit_pelanggan']:,.0f} "
                        f"retur={c['retur']:,.0f} kas_keluar={c['kas_keluar']:,.0f} "
                        f"hut_sup={c['pelunasan_hutang_supplier']:,.0f}"
                    )
                if reasons:
                    bad_rows.append((row, reasons))
                    for reason in reasons:
                        emit(f"     - {reason}")
    finally:
        conn.close()

    if args.fix and bad_rows:
        emit("\n" + "=" * 110)
        emit(f"FIX SCOPED ({'APPLY' if args.apply else 'DRY-RUN'})")
        emit("=" * 110)
        for row, _ in bad_rows:
            rc = run_fix(row, args.apply)
            if rc:
                return rc

    emit("\n" + "=" * 110)
    if bad_rows:
        emit(f"SELESAI: {len(bad_rows)} cabang/bulan perlu perhatian.")
        return 1
    emit("SELESAI: semua cocok dalam tolerance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
