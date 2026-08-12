"""Sync POS header + detail per tanggal via /laporan/lap_penjualan/rekap + keywords.

Sumber ini akurat mengikuti dashboard: untuk tiap (cabang, tanggal), daftar nota
diambil dari /laporan/lap_penjualan/rekap (parameter tanggal dihormati, tidak
seperti pagination /transaksi/pos yang bisa kehilangan nota lama), lalu tiap nota
dicari header-nya via /transaksi/pos?keywords= (menyaring beneran). Hasil
di-upsert ke brighter_pos + brighter_pos_detail.

Dipakai sebagai modul oleh sync_dashboard.py, atau langsung via CLI:

    python sync_pos_rekap.py --env --cabang-ids 1,2,4,5,7 --tanggal-awal 2026-01-05 --tanggal-akhir 2026-01-05
"""

import argparse
import dataclasses
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import httpx

sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager
from sync_pos import map_header, map_detail, fetch_pos_detail, insert_batch_upsert
from sync_pos_by_nota import find_pos_by_nota, api_get
from sync_saldo_kas_harian import load_cabang_urls


def build_server_auth(cfg, cabang_ids, urls):
    """Satu AuthManager per server (token tidak bisa lintas domain)."""
    server_cfg, server_auth = {}, {}
    cabang_cfg, cabang_auth = {}, {}
    for cid in cabang_ids:
        url = urls.get(cid)
        key = url or cfg.base_url
        if key not in server_cfg:
            c = dataclasses.replace(cfg, base_url=url) if url else cfg
            server_cfg[key] = c
            server_auth[key] = AuthManager(c)
        cabang_cfg[cid] = server_cfg[key]
        cabang_auth[cid] = server_auth[key]
    return cabang_cfg, cabang_auth


def fetch_rekap_notas(client, config, auth, tanggal, cabang_id):
    """Daftar no_bukti + gross dari lap_penjualan/rekap untuk satu hari."""
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


def needs_pos_sync(db, cabang_id, tanggal):
    """True bila belum ada baris brighter_pos untuk (cabang, tanggal)."""
    with db.conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM brighter_pos WHERE cabang_id=%s AND tanggal=%s", (cabang_id, tanggal))
        return (cur.fetchone()["n"] or 0) == 0


def gap_dates(db, cabang_ids, tgl_awal, tgl_akhir):
    """Tanggal tanpa data brighter_pos per cabang di rentang [awal, akhir]."""
    cur = db.conn.cursor()
    d = date.fromisoformat(tgl_awal)
    end = date.fromisoformat(tgl_akhir)
    dates = []
    while d <= end:
        dates.append(d.isoformat())
        d += timedelta(days=1)
    out = {}
    for cid in cabang_ids:
        present = set()
        if dates:
            cur.execute(
                "SELECT DISTINCT tanggal FROM brighter_pos WHERE cabang_id=%s AND tanggal BETWEEN %s AND %s",
                (cid, dates[0], dates[-1]))
            present = {r["tanggal"].isoformat() if hasattr(r["tanggal"], "isoformat") else str(r["tanggal"]) for r in cur.fetchall()}
        out[cid] = sorted(set(dates) - present)
    return out


def sync_pos_day(client, config, auth, db, cabang_id, tanggal, workers):
    """Sync satu hari penuh (rekap -> keyword -> detail -> upsert). Return stats dict."""
    notas, gross = fetch_rekap_notas(client, config, auth, tanggal, cabang_id)
    if not notas:
        return {"nota": 0, "found": 0, "header": 0, "detail": 0, "gross": 0.0}
    notas = list(dict.fromkeys(notas))

    found = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(find_pos_by_nota, client, config, auth, n): n for n in notas}
        for fut in as_completed(futs):
            try:
                rec = fut.result()
            except Exception:
                continue
            if rec is not None:
                found[futs[fut]] = rec

    headers_by_cabang = {}
    items_by_cabang = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_pos_detail, config, auth, h["jproduk_id"]): h for h in found.values()}
        for fut in as_completed(futs):
            h = futs[fut]
            cabang = h.get("jproduk_cabang_id") or cabang_id
            headers_by_cabang.setdefault(cabang, []).append(map_header(h, cabang))
            try:
                items = fut.result()
                for it in items:
                    items_by_cabang.setdefault(cabang, []).append(map_detail(it, cabang))
            except Exception:
                pass

    n_header = 0
    for cabang, hdrs in headers_by_cabang.items():
        insert_batch_upsert(db, "brighter_pos", hdrs)
        n_header += len(hdrs)
    n_detail = 0
    for cabang, items in items_by_cabang.items():
        insert_batch_upsert(db, "brighter_pos_detail", items)
        n_detail += len(items)
    return {"nota": len(notas), "found": len(found), "header": n_header, "detail": n_detail, "gross": gross}


def sync_pos_range(cfg, urls, cabang_ids, tgl_awal=None, tgl_akhir=None, workers=5,
                   force=False, force_dates=None, dates=None):
    """Sync POS untuk rentang tanggal (atau daftar tanggal eksplisit per cabang).

    - dates: dict {cabang_id: [tgl,...]} bila ingin proses tanggal tertentu saja.
    - force_dates: set tanggal (ISO) yang WAJIB di-sync ulang walau sudah ada
      (dipakai untuk jendela hari terakhir agar menangkap perubahan/edit).
    - force: sync ulang semua tanggal yang diproses.
    Return dict stats per cabang.
    """
    cabang_cfg, cabang_auth = build_server_auth(cfg, cabang_ids, urls)
    db = DatabaseManager(cfg, target_db="csb")
    db.connect()
    stats = {cid: {"hari": 0, "nota": 0, "found": 0, "header": 0, "detail": 0, "gross": 0.0} for cid in cabang_ids}

    if dates is not None:
        day_list = {cid: dates.get(cid, []) for cid in cabang_ids}
    else:
        day_list = {}
        d = date.fromisoformat(tgl_awal or "2026-01-01")
        end = date.fromisoformat(tgl_akhir or date.today().isoformat())
        all_days = []
        while d <= end:
            all_days.append(d.isoformat())
            d += timedelta(days=1)
        for cid in cabang_ids:
            day_list[cid] = list(all_days)

    force_dates = force_dates or set()
    for cid in cabang_ids:
        for ts in day_list[cid]:
            if not (force or ts in force_dates) and not needs_pos_sync(db, cid, ts):
                continue
            client = httpx.Client(base_url=cabang_cfg[cid].base_url, timeout=cfg.request_timeout, follow_redirects=True)
            try:
                r = sync_pos_day(client, cabang_cfg[cid], cabang_auth[cid], db, cid, ts, workers)
            finally:
                client.close()
            s = stats[cid]
            s["hari"] += 1
            for k in ("nota", "found", "header", "detail"):
                s[k] += r[k]
            s["gross"] += r["gross"]
            print(f"  cb{cid} {ts}: {r['nota']} nota, {r['found']} ketemu, "
                  f"{r['header']} header, {r['detail']} detail, gross={r['gross']:,.0f}")
    db.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Sync POS header+detail via lap_penjualan/rekap + keywords")
    parser.add_argument("-e", "--env", action="store_true")
    parser.add_argument("--cabang-ids", default=None, help="Comma-separated (default: semua aktif)")
    parser.add_argument("--tanggal-awal", default="2026-01-01")
    parser.add_argument("--tanggal-akhir", default=date.today().isoformat())
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--force", action="store_true", help="Sync ulang walau tanggal sudah ada")
    args = parser.parse_args()

    cfg = Config.from_env()
    db = DatabaseManager(cfg, target_db="csb")
    db.connect()
    urls = load_cabang_urls(db)
    cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()] if args.cabang_ids else sorted(urls)
    db.close()

    print(f"Rentang {args.tanggal_awal}..{args.tanggal_akhir}, cabang {cabang_ids}")
    stats = sync_pos_range(cfg, urls, cabang_ids, args.tanggal_awal, args.tanggal_akhir, args.workers, args.force)
    for cid in cabang_ids:
        s = stats[cid]
        print(f"  cb{cid}: {s['hari']} hari, {s['nota']} nota, {s['header']} header, {s['detail']} detail, gross={s['gross']:,.0f}")


if __name__ == "__main__":
    main()
