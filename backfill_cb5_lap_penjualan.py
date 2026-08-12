"""Backfill POS header + detail untuk cb5 02-05 Jan 2026 (data yang hanya ada di
laporan lap_penjualan, tidak terjangkau pagination /transaksi/pos).

Alur:
1. Baca daftar no_bukti per hari dari /laporan/lap_penjualan/rekap (tanggal dihormati).
2. Untuk tiap no_bukti: cari header via /transaksi/pos?keywords= (parameter ini
   benar-benar menyaring, bisa menjangkau nota lama yang tidak muncul di pagination).
3. Ambil detail via /transaksi/pos/{id}/detail_pos.
4. Upsert header ke brighter_pos + detail ke brighter_pos_detail.
5. Setelah itu jalankan  php artisan pos:reconcile-missing --cabang 5 --date YYYY-MM-DD
   untuk membuat pos_transactions.

Contoh:
    python backfill_cb5_lap_penjualan.py --env --tanggal-awal 2026-01-02 --tanggal-akhir 2026-01-05
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import httpx

sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager
from sync_pos import map_header, map_detail, fetch_pos_detail, insert_batch_upsert
from sync_pos_by_nota import find_pos_by_nota, api_get


def fetch_rekap_notas(client, config, auth, tanggal, cabang_id):
    data = api_get(client, config, auth, "/laporan/lap_penjualan/rekap", {
        "tanggal_awal": tanggal, "tanggal_akhir": tanggal,
        "cabang_id": str(cabang_id), "group_by": "tanggal",
    })
    d = data.get("data") or {}
    groups = d.get("grouped_by_filter") or []
    notas = []
    total_gross = 0.0
    for g in groups:
        for t in g.get("grouped_transaksi") or []:
            nb = (t.get("jproduk_nobukti") or "").strip().upper()
            if not nb:
                continue
            paid = (t.get("tunai_rp") or 0) + (t.get("transfer_rp") or 0) + (t.get("card_rp") or 0) \
                 + (t.get("qris_barcode_rp") or 0) + (t.get("qris_scan_rp") or 0) + (t.get("wallet_rp") or 0)
            total_gross += paid + (t.get("hutang_rp") or 0)
            notas.append(nb)
    return notas, total_gross


def main():
    parser = argparse.ArgumentParser(description="Backfill POS header+detail cb5 02-05 Jan via lap_penjualan rekap + keywords")
    parser.add_argument("-e", "--env", action="store_true", help="Load config dari env")
    parser.add_argument("--tanggal-awal", default="2026-01-02", help="YYYY-MM-DD")
    parser.add_argument("--tanggal-akhir", default="2026-01-05", help="YYYY-MM-DD")
    parser.add_argument("--cabang-id", type=int, default=5)
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    config = Config.from_env()
    auth = AuthManager(config)
    db = DatabaseManager(config, target_db="csb")
    db.connect()

    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout, follow_redirects=True)

    d = date.fromisoformat(args.tanggal_awal)
    end = date.fromisoformat(args.tanggal_akhir)
    semua_notas = []
    while d <= end:
        notas, gross = fetch_rekap_notas(client, config, auth, d.isoformat(), args.cabang_id)
        print(f"{d.isoformat()}: {len(notas)} nota, gross={gross:,.0f}")
        semua_notas.extend(notas)
        d += timedelta(days=1)

    semua_notas = list(dict.fromkeys(semua_notas))
    print(f"Total nota unik: {len(semua_notas)}")

    found = {}
    not_found = []
    errors = []
    print("Fase 1: cari header via keywords...")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(find_pos_by_nota, client, config, auth, n): n for n in semua_notas}
        done = 0
        for fut in as_completed(futs):
            n = futs[fut]
            done += 1
            try:
                rec = fut.result()
            except Exception as e:
                errors.append((n, str(e)))
                continue
            if rec is not None:
                found[n] = rec
            else:
                not_found.append(n)
            if done % 100 == 0 or done == len(semua_notas):
                print(f"  [progress] {done}/{len(semua_notas)}: {len(found)} ketemu, {len(not_found)} tidak, {len(errors)} error")

    print(f"Fase 1 selesai: {len(found)} ketemu, {len(not_found)} tidak, {len(errors)} error")
    if not_found:
        print("  Not found:", not_found[:50])
    if errors:
        print("  Errors:", errors[:20])

    headers_by_cabang = {}
    items_by_cabang = {}
    print("Fase 2: ambil detail + siapkan upsert...")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_pos_detail, config, auth, h["jproduk_id"]): h for h in found.values()}
        done = 0
        for fut in as_completed(futs):
            h = futs[fut]
            done += 1
            cabang = h.get("jproduk_cabang_id") or 1
            headers_by_cabang.setdefault(cabang, []).append(map_header(h, cabang))
            try:
                items = fut.result()
                for it in items:
                    items_by_cabang.setdefault(cabang, []).append(map_detail(it, cabang))
            except Exception:
                pass
            if done % 100 == 0 or done == len(found):
                print(f"  [detail] {done}/{len(found)}")

    for cabang, hdrs in sorted(headers_by_cabang.items()):
        print(f"  Upsert brighter_pos (cabang {cabang}): {len(hdrs)} header")
        insert_batch_upsert(db, "brighter_pos", hdrs)
    for cabang, items in sorted(items_by_cabang.items()):
        print(f"  Upsert brighter_pos_detail (cabang {cabang}): {len(items)} baris")
        insert_batch_upsert(db, "brighter_pos_detail", items)

    db.close()
    client.close()

    print("\n" + "=" * 50)
    print("SELESAI - BACKFILL CB5 LAP PENJUALAN")
    print(f"Header di-upsert: {sum(len(v) for v in headers_by_cabang.values())}")
    print(f"Detail di-upsert: {sum(len(v) for v in items_by_cabang.values())}")
    print("Langkah berikutnya: php artisan pos:reconcile-missing --cabang 5 --date YYYY-MM-DD")
    print("=" * 50)


if __name__ == "__main__":
    main()
