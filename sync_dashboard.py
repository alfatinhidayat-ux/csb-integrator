"""Orkestrator: SATU program untuk menyinkronkan dashboard & memastikan semua match.

Alur (incremental + isi gap):
  1. Rencana tanggal per cabang = jendela N hari terakhir (di-sync ulang penuh
     untuk menangkap edit/void) + tanggal gap yang belum ada di brighter_pos.
     Dengan --full: seluruh rentang sejak --since.
  2. Sync POS penjualan via lap_penjualan/rekap + keywords (sumber akurat).
  3. Sync retur penjualan   -> sync_retur_penjualan.py
  4. Sync penerimaan rekap  -> sync_saldo_kas_harian.py
  5. Sync pelunasan piutang -> sync_pelunasan.py (opsional: sync_finance.py via --full-finance)
  6. Reconcile/apply ke tabel Clarify:
     - POS -> pos_transactions
     - Retur -> retur_penjualan
     - Piutang -> piutang + piutang_pelunasan
     - Supplier hutang -> supplier_hutang_pelunasan* lewat sync_finance.py
  7. Audit SEMUA jenis dashboard -> audit_dashboard.py (exit != 0 bila ada selisih)

Contoh:
    python sync_dashboard.py --env --days 3
    python sync_dashboard.py --env --full
    python sync_dashboard.py --env --days 1 --skip-retur --skip-penerimaan
"""

import argparse
import os
import subprocess
import sys
from datetime import date, timedelta

sys.path.insert(0, os.getcwd())

from config import Config
from db import DatabaseManager
from sync_saldo_kas_harian import load_cabang_urls
from sync_pos_rekap import sync_pos_range, gap_dates

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "csb-backend-api")

# Cabang yang baru aktif di bulan tertentu; tanggal sebelum itu diabaikan
# (lihat AGENTS.md: cabang 7 aktif mulai Mei 2026, sebelum itu tidak punya data).
ACTIVE_SINCE = {7: "2026-05-01"}


def cabang_active_dates(cid, dates):
    """Filter tanggal sebelum cabang aktif (ACTIVE_SINCE)."""
    since = ACTIVE_SINCE.get(cid)
    if not since:
        return dates
    return [d for d in dates if d >= since]


def date_range(s, e):
    out = []
    d = date.fromisoformat(s)
    end = date.fromisoformat(e)
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def run_step(title, cmd, dry_run=False, cwd=None):
    print(f"\n[{title}]")
    print(f"  $ {' '.join(cmd)}")
    if dry_run:
        return 0
    return subprocess.run(cmd, cwd=cwd or os.getcwd(), env=dict(os.environ, PYTHONIOENCODING="utf-8")).returncode


def main():
    parser = argparse.ArgumentParser(description="Orkestrator sync dashboard Brighter (1 program, semua match)")
    parser.add_argument("-e", "--env", action="store_true")
    parser.add_argument("--cabang-ids", default=None, help="Comma-separated (default: semua aktif)")
    parser.add_argument("--tanggal-akhir", default=date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=3, help="Jendela hari terakhir yang di-sync ulang penuh (default 3)")
    parser.add_argument("--since", default="2026-01-01", help="Batas bawah untuk deteksi gap / mode --full")
    parser.add_argument("--full", action="store_true", help="Sync seluruh rentang --since..--tanggal-akhir")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--skip-pos", action="store_true")
    parser.add_argument("--skip-retur", action="store_true")
    parser.add_argument("--skip-penerimaan", action="store_true")
    parser.add_argument("--skip-pelunasan", action="store_true")
    parser.add_argument("--skip-clarify", action="store_true", help="Lewati landing/reconcile ke tabel aplikasi Clarify")
    parser.add_argument("--skip-reconcile", action="store_true")
    parser.add_argument("--skip-audit", action="store_true")
    parser.add_argument("--full-finance", action="store_true", help="Jalankan sync_finance.py (pelunasan piutang, full & lambat)")
    parser.add_argument("--backend-dir", default=BACKEND_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Hanya tampilkan rencana, tanpa menulis")
    args = parser.parse_args()

    cfg = Config.from_env()
    db = DatabaseManager(cfg, target_db="csb")
    db.connect()
    urls = load_cabang_urls(db)
    cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()] if args.cabang_ids else sorted(urls)
    if not cabang_ids:
        print("[error] tidak ada cabang aktif.")
        sys.exit(1)

    akhir = args.tanggal_akhir
    since = args.since

    # ── Rencana tanggal ──
    if args.full:
        plan = {cid: cabang_active_dates(cid, date_range(since, akhir)) for cid in cabang_ids}
        force_dates = set(date_range(since, akhir))
    else:
        win_start = (date.fromisoformat(akhir) - timedelta(days=args.days - 1)).isoformat()
        recent = set(date_range(win_start, akhir))
        gaps = gap_dates(db, cabang_ids, since, akhir)
        plan = {}
        force_dates = set()
        for cid in cabang_ids:
            dates = sorted(set(recent) | set(gaps[cid]))
            plan[cid] = cabang_active_dates(cid, dates)
            force_dates |= recent
    db.close()

    total_days = sum(len(v) for v in plan.values())
    print("=" * 60)
    print(f"RENCANA ({'FULL' if args.full else 'INCREMENTAL'}): cabang {cabang_ids}")
    print(f"  rentang: {since} .. {akhir}, jendela {args.days} hari, total {total_days} (cabang,tanggal)")
    if not args.full:
        for cid in cabang_ids:
            g = [x for x in plan[cid] if x not in force_dates]
            print(f"  cb{cid}: {len(plan[cid])} hari ({len([x for x in plan[cid] if x in force_dates])} jendela + {len(g)} gap)")
    print("=" * 60)

    if args.dry_run:
        print("[dry-run] Rencana selesai. Tidak ada perubahan.")
        return 0

    py = sys.executable
    artifacts = []

    # ── 1. Sync POS penjualan ──
    if not args.skip_pos:
        print("\n[1/6] Sync POS penjualan (rekap + keywords)...")
        sync_pos_range(cfg, urls, cabang_ids, tgl_awal=None, tgl_akhir=None,
                       workers=args.workers, force=False,
                       force_dates=force_dates, dates=plan)
    else:
        print("\n[1/6] SKIP sync POS.")

    min_date = min(date.fromisoformat(d) for lst in plan.values() for d in lst)
    lo = min_date.isoformat()

    # ── 2. Sync retur penjualan ──
    if not args.skip_retur:
        run_step("2/6 Sync retur penjualan",
                 [py, "sync_retur_penjualan.py", "--env",
                  "--cabang-ids", ",".join(map(str, cabang_ids)),
                  "--tanggal-awal", lo, "--tanggal-akhir", akhir])
    else:
        print("\n[2/6] SKIP sync retur.")

    # ── 3. Sync penerimaan rekap / kas ──
    if not args.skip_penerimaan:
        run_step("3/6 Sync penerimaan rekap + saldo kas",
                 [py, "sync_saldo_kas_harian.py", "--env",
                  "--cabang-ids", ",".join(map(str, cabang_ids)),
                  "--tanggal-awal", lo, "--tanggal-akhir", akhir])
    else:
        print("\n[3/6] SKIP sync penerimaan.")

    # ── 3b. Sync pelunasan piutang (default; endpoint tanpa filter tanggal → fetch all per cabang) ──
    if not args.skip_pelunasan:
        run_step("3b/6 Sync pelunasan piutang",
                 [py, "sync_pelunasan.py", "--env",
                  "--cabang-ids", ",".join(map(str, cabang_ids))])
    else:
        print("\n[3b/6] SKIP sync pelunasan piutang.")

    # ── 3c. (opsional) sync_finance.py untuk tabel finance lain (supplier/pembelian/hutang) ──
    if args.full_finance:
        run_step("3c/6 Sync finance (pelunasan hutang/piutang detail, full)",
                 [py, "sync_finance.py", "--env",
                  "--cabang-ids", ",".join(map(str, cabang_ids))])
    else:
        print("\n[3c/6] SKIP sync finance (gunakan --full-finance bila mau segarkan tabel finance lain).")

    # ── 3d. Landing data staging ke tabel aplikasi Clarify ──
    # Data migrasi harus tetap bisa dibaca lewat alur Clarify. Langkah ini data-only:
    # tidak posting kas_bank untuk retur/piutang/supplier hutang legacy.
    if not args.skip_clarify:
        if not args.skip_pelunasan:
            rc = run_step("3d/6 Clarify piutang penjualan",
                          ["php", "artisan", "migrate:legacy-piutang"],
                          cwd=args.backend_dir)
            artifacts.append(("clarify piutang", rc))
            if rc == 0:
                rc = run_step("3d/6 Clarify pelunasan piutang",
                              ["php", "artisan", "backfill:piutang-pelunasan"],
                              cwd=args.backend_dir)
                artifacts.append(("clarify pelunasan piutang", rc))

        if not args.skip_retur:
            rc = run_step("3d/6 Clarify retur penjualan",
                          [py, "migrate_retur_to_app.py", "--yes"])
            artifacts.append(("clarify retur", rc))
    else:
        print("\n[3d/6] SKIP landing Clarify.")

    # ── 4. Reconcile pos_transactions ──
    if not args.skip_reconcile:
        print("\n[4/6] Reconcile pos_transactions (php artisan pos:reconcile-missing --refresh per tanggal)...")
        for cid in cabang_ids:
            for tgl in plan[cid]:
                cmd = ["php", "artisan", "pos:reconcile-missing",
                       "--cabang", str(cid), "--date", tgl, "--refresh"]
                rc = run_step(f"reconcile cb{cid} {tgl}", cmd, dry_run=False, cwd=args.backend_dir)
                if rc != 0:
                    print(f"[error] reconcile cb{cid} {tgl} gagal (rc={rc})")
                    artifacts.append((f"reconcile cb{cid} {tgl}", rc))
    else:
        print("\n[4/6] SKIP reconcile.")

    # ── 5. Audit semua jenis dashboard ──
    if not args.skip_audit:
        print("\n[5/6] Audit dashboard...")
        cmd = [py, "audit_dashboard.py", since, akhir, "--cabang-ids", ",".join(map(str, cabang_ids))]
        rc = run_step("audit dashboard", cmd, dry_run=False)
        artifacts.append(("AUDIT", rc))
    else:
        print("\n[5/6] SKIP audit.")

    print("\n" + "=" * 60)
    print("SELESAI - SYNC DASHBOARD")
    if artifacts:
        for name, rc in artifacts:
            print(f"  {name}: {'OK' if rc == 0 else 'FAIL(rc=' + str(rc) + ')'}")
    if any(rc != 0 for _, rc in artifacts):
        sys.exit(1)
    print("=" * 60)


if __name__ == "__main__":
    main()
