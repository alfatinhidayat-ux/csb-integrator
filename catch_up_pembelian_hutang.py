"""Catch-up pembelian & pelunasan hutang (berjalan berulang saat migrasi berjalan).

Selama proses migrasi ke app berlangsung, transaksi baru terus masuk di
Brighter (sync berjalan tanpa henti). Pipiline ini mengejar "data yang
tertinggal" lewat langkah idempotent — boleh dijalankan kapan saja tanpa
menduplikat:

    1. Upsert master supplier ke csb_db.supplier (per server).
    2. Sync header pembelian -> brighter_persediaan_pembelian (upsert).
    3. Sync pelunasan hutang (header + detail + foto) -> brighter_transaksi_*
    4. Refresh staging detail pembelian (brighter_persediaan_pembelian_detail)
       via cek_produk_pembelian.py (delete + data-latest per cabang).
    5. Migrasi pembelian/pembelian_detail ke app (migrate_pembelian_to_app.py),
       numanya insert dokumen baru + replace detail dokumen lama.
    6. Rekon pembelian vs pelunasan hutang (rekon_pembelian_hutang.py).

Karena langkah 2-5 idempotent, dokumen yang baru muncul setelah tgl migrasi
terakhir akan ketangkap, dan yang sudah ada tidak diduplikat.

Usage:
    python catch_up_pembelian_hutang.py --env
    python catch_up_pembelian_hutang.py --env --cabang-ids 1,5
    python catch_up_pembelian_hutang.py --env --skip-detail --skip-rekon
"""
from __future__ import annotations

import argparse
import atexit
import dataclasses
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager

from sync_finance import (
    ENDPOINT_META,
    TABLES,
    discover_cabangs,
    fetch_all_pages,
    load_cabang_urls,
    map_csb_supplier,
    map_record,
    sync_headers,
    upsert_batch,
    upsert_csb_supplier,
)

PY = sys.executable


def run_step(title, script, flags=(), check=True):
    print("\n" + "=" * 70)
    print(f"[{title}]")
    print("=" * 70)
    cmd = [PY, script, *flags]
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.run(cmd, env=env)
    if proc.returncode != 0 and check:
        print(f"\n[GAGAL] langkah '{title}' exit {proc.returncode}. Pipeline berhenti.")
        sys.exit(proc.returncode)
    return proc.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Catch-up pembelian + pelunasan hutang (idempotent, jalankan ulang saat migrasi)."
    )
    parser.add_argument("-e", "--env", action="store_true",
                        help="Load config dari environment (BRIGHTER_*)")
    parser.add_argument("--cabang-ids", default=None,
                        help="Comma-separated cabang IDs; default semua aktif")
    parser.add_argument("--workers", type=int, default=5,
                        help="ThreadPool workers untuk child pelunasan (default 5)")
    parser.add_argument("--skip-detail", action="store_true",
                        help="Lewati refresh staging detail pembelian (berat, sekali sehari cukup)")
    parser.add_argument("--skip-rekon", action="store_true",
                        help="Lewati rekon pembelian vs pelunasan di akhir")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    config = Config.from_env()
    db = DatabaseManager(config, target_db="csb")

    def _close_safe():
        try:
            db.close()
        except Exception:
            pass
    atexit.register(_close_safe)

    # Flag per sub-script (tak semua kenal --env/--verbose).
    cabang_flags = []
    if args.cabang_ids:
        cabang_flags = ["--cabang-ids", args.cabang_ids]
    detail_flags = list(cabang_flags)
    if args.verbose:
        detail_flags.append("--verbose")
    # cek_produk_pembelian & migrate_pembelian tak kenal --env (otomatis Config.from_env())
    migrate_flags = ["--run"] + cabang_flags
    rekon_flags = ["--env"] + cabang_flags

    # Pre-flight: pastikan kredensial API valid sebelum pipeline panjang.
    auth_default = AuthManager(config)
    db.connect()
    try:
        auth_default.ensure_token()
    except Exception as e:
        print(f"\n[Pre-flight] Tidak dapat menghubungi API Brighter: {e}")
        print("  Cek koneksi/VPN/.env lalu jalankan ulang.")
        db.close()
        sys.exit(1)

    cabang_urls = load_cabang_urls(db)
    if args.cabang_ids:
        cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]
    else:
        try:
            cabang_ids = discover_cabangs(config, auth_default, args.verbose)
        except Exception as e:
            print(f"Error discover cabang: {e}. Default [1]")
            cabang_ids = [1]
        if not cabang_ids:
            cabang_ids = [1]

    print(f"Cabang: {cabang_ids}")
    for cid in cabang_ids:
        print(f"   cabang {cid} -> base_url: {cabang_urls.get(cid) or config.base_url}")

    # Per-server config/auth (satu login per URL server).
    server_cfg, server_auth, cabang_cfg, cabang_auth = {}, {}, {}, {}
    for cid in cabang_ids:
        url = cabang_urls.get(cid)
        key = url or config.base_url
        if key not in server_cfg:
            cfg = dataclasses.replace(config, base_url=url) if url else config
            server_cfg[key] = cfg
            server_auth[key] = AuthManager(cfg)
        cabang_cfg[cid] = server_cfg[key]
        cabang_auth[cid] = server_auth[key]

    totals = {name: 0 for name in TABLES}
    supplier_total = 0

    # Step 1: Master supplier per server -> csb_db.supplier (upsert, relasi utk pembelian).
    print("\n[1/6] Master supplier per server...")
    server_repr = {}
    for cid in cabang_ids:
        server_repr.setdefault(cabang_cfg[cid].base_url, cid)
    for url, rep_cid in server_repr.items():
        c_cfg = cabang_cfg[rep_cid]
        c_auth = cabang_auth[rep_cid]
        mapping = [c for c, u in cabang_cfg.items() if u.base_url == url]
        try:
            rows = fetch_all_pages(
                c_cfg, c_auth,
                ENDPOINT_META["supplier"]["path"], {},
                dict(ENDPOINT_META["supplier"]["params"]),
                None, None, args.verbose,
            )
            mapped = [map_csb_supplier(r, rep_cid) for r in rows if r.get("supplier_id") is not None]
            n = upsert_csb_supplier(db, mapped, rep_cid, mapping)
            supplier_total += n
            print(f"       -> {url}: {n} supplier upsert")
        except Exception as e:
            print(f"       -> ERROR supplier @ {url}: {e}")

    count_ceks = 0
    for c_id in cabang_ids:
        print(f"\n--- Cabang {c_id} ---")
        try:
            db.reconnect()
        except Exception:
            pass
        cfg_c = cabang_cfg[c_id]
        auth_c = cabang_auth[c_id]

        # Step 2: header pembelian.
        print("  [2/6] Faktur pembelian (header)...")
        try:
            pembelian_rows = sync_headers(cfg_c, auth_c, db, "pembelian", c_id, args.verbose)
        except Exception as e:
            print(f"       -> ERROR pembelian: {e}")
            pembelian_rows = []
        mapped = [map_record("pembelian", r, c_id) for r in pembelian_rows]
        totals["pembelian"] += upsert_batch(db, TABLES["pembelian"], mapped, c_id)
        print(f"       -> {len(pembelian_rows)} records")

        # Step 3: pelunasan hutang (header + detail + foto).
        print("  [3/6] Pelunasan hutang (header + detail + foto)...")
        try:
            hutang_rows = sync_headers(cfg_c, auth_c, db, "pelunasan_hutang", c_id, args.verbose)
        except Exception as e:
            print(f"       -> ERROR pelunasan hutang header: {e}")
            hutang_rows = []
        hutang_mapped = [map_record("pelunasan_hutang", r, c_id) for r in hutang_rows]
        totals["pelunasan_hutang"] += upsert_batch(db, TABLES["pelunasan_hutang"], hutang_mapped, c_id)
        print(f"       -> {len(hutang_rows)} header")

        for child, label in (
            ("pelunasan_hutang_detail", "detail"),
            ("pelunasan_hutang_foto", "foto"),
        ):
            child_rows = []
            if not hutang_rows:
                continue
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = {
                    ex.submit(
                        fetch_all_pages, cfg_c, auth_c,
                        ENDPOINT_META[child]["path"],
                        {"id": h["fhutang_id"]},
                        ENDPOINT_META[child]["params"],
                        None, None, args.verbose,
                    ): h
                    for h in hutang_rows
                }
                for fut in as_completed(futs):
                    h = futs[fut]
                    try:
                        for r in fut.result():
                            child_rows.append(map_record(
                                child, r, c_id, extra={"fhutang_id": h["fhutang_id"]},
                            ))
                    except Exception as e:
                        print(f"    error {label} fhutang {h.get('fhutang_id')}: {e}")
            totals[child] += upsert_batch(db, TABLES[child], child_rows, c_id)
            print(f"       -> {len(child_rows)} {label}")
        count_ceks += 1

    print(f"\nSync staging selesai. Cabang diprocess: {count_ceks}")
    for name, table in TABLES.items():
        if totals[name]:
            print(f"  {name:28s} -> {totals[name]} records")

    # Step 4: refresh staging detail pembelian (delete + data-latest).
    if not args.skip_detail:
        run_step("4/6 Fetching staging detail pembelian (cek_produk_pembelian)",
                 "cek_produk_pembelian.py", detail_flags)

    # Step 5: migrasi ke app (idempotent).
    run_step("5/6 Migrasi pembelian -> app (pembelian/pembelian_detail)",
             "migrate_pembelian_to_app.py", migrate_flags)

    # Step 6: rekon.
    if not args.skip_rekon:
        run_step("6/6 Rekon pembelian vs pelunasan hutang",
                 "rekon_pembelian_hutang.py", rekon_flags)

    print("\n" + "=" * 70)
    print("CATCH-UP PEMBELIAN & PELUNASAN HUTANG SELESAI.")
    print("Lihat log + hasil rekon di atas; kalau semua 0/matched artinya sudah catch-up.")
    print("=" * 70)


if __name__ == "__main__":
    main()