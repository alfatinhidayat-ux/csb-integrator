"""Audit read-only: bandingkan SEMUA jenis dashboard (rekap_dashboard API) vs DB
untuk rentang tanggal per cabang. Tidak menulis apa pun.

Pemetaan jenis -> sumber DB:
  - penjualan                       : pos_transactions (gross = Σ total_amount paid/partial, bukan piutang)
  - retur_jual                      : brighter_retur_penjualan (status Tertutup)
  - pelunasan_piutang_penjualan     : brighter_transaksi_pelunasan_piutang (stat_dok Tertutup, Σ bayar)
  - kas_keluar_pengeluaran_lain     : akuntansi_kasbank_keluar (kasbank_pengeluaran_lain=1, Σ kasbank_keluar_total)

Contoh:
    python audit_dashboard.py 2026-01-01 2026-01-31
    python audit_dashboard.py 2026-01-05 2026-01-05 --cabang-ids 1,5
"""

import dataclasses
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.getcwd())

import httpx
from config import Config
from auth import AuthManager
from db import DatabaseManager
from sync_saldo_kas_harian import load_cabang_urls

TGL_AWAL = sys.argv[1] if len(sys.argv) > 1 else "2026-01-01"
TGL_AKHIR = sys.argv[2] if len(sys.argv) > 2 else "2026-01-31"
CABANG_FILTER = None
for i, a in enumerate(sys.argv):
    if a == "--cabang-ids" and i + 1 < len(sys.argv):
        CABANG_FILTER = [int(x.strip()) for x in sys.argv[i + 1].split(",") if x.strip()]

JENIS_DB = [
    ("penjualan", "pos_transactions",
     "SELECT IFNULL(SUM(total_amount),0) v FROM pos_transactions WHERE cabang_id=%s AND DATE(waktu_transaksi)=%s AND payment_status IN ('paid','partial') AND payment_method<>'piutang'",
     "gross"),
    ("retur_jual", "brighter_retur_penjualan",
     "SELECT IFNULL(SUM(total_rp),0) v FROM brighter_retur_penjualan WHERE cabang_id=%s AND tanggal=%s AND status_dokumen='Tertutup'",
     "total_rp"),
    ("pelunasan_piutang_penjualan", "brighter_transaksi_pelunasan_piutang",
     "SELECT IFNULL(SUM(bayar),0) v FROM brighter_transaksi_pelunasan_piutang WHERE cabang_id=%s AND tanggal=%s AND stat_dok='Tertutup'",
     "total_rp"),
    ("kas_keluar_pengeluaran_lain", "akuntansi_kasbank_keluar",
     "SELECT IFNULL(SUM(kasbank_pengeluaran_lain),0) v FROM akuntansi_kasbank_keluar WHERE cabang_id=%s AND kasbank_tanggal=%s AND kasbank_pengeluaran_lain>0",
     "total_rp"),
]


def fetch_dash_all(cfg, auth, cabang_id, tanggal, retries=3):
    """Semua jenis dashboard untuk satu (cabang, tanggal) sebagai dict jenis->rekap."""
    auth.ensure_token()
    url = cfg.base_url + "/laporan/dashboard/rekap_dashboard"
    params = {"tanggal_awal": tanggal, "tanggal_akhir": tanggal, "cabang_id": str(cabang_id)}
    for att in range(retries):
        try:
            r = httpx.get(url, params=params, headers=auth.get_headers(), timeout=60)
            break
        except httpx.TransportError:
            if att == retries - 1:
                raise
            time.sleep(5)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    out = {}
    for b in (r.json().get("data") or []):
        out[b["jenis_dashboard"]] = b.get("rekap") or {}
    return out


def audit_day(cur, cfg, auth, cid, ts):
    """Return list of {jenis, dash, db, diff} untuk satu hari."""
    dash_all = fetch_dash_all(cfg, auth, cid, ts)
    rows = []
    for jenis, table, sql, dash_key in JENIS_DB:
        rk = dash_all.get(jenis)
        if dash_key == "gross":
            dash = (rk.get("total_rp") or 0) + (rk.get("total_piutang_rp") or 0) if rk else 0.0
        else:
            dash = (rk.get(dash_key) or 0) if rk else 0.0
        cur.execute(sql, (cid, ts))
        dbv = float(cur.fetchone()["v"] or 0)
        rows.append({"cabang": cid, "tanggal": ts, "jenis": jenis,
                     "dash": dash, "db": dbv, "diff": dbv - dash})
    return rows


def audit_range(cabang_ids, tgl_awal, tgl_akhir):
    """Return (rows, totals) di mana totals = per jenis {dash, db, diff}."""
    cfg = Config.from_env()
    db = DatabaseManager(cfg, target_db="csb")
    db.connect()
    cur = db.conn.cursor()
    urls = load_cabang_urls(db)
    server_cfg, server_auth = {}, {}
    for cid in cabang_ids:
        url = urls.get(cid)
        key = url or cfg.base_url
        if key not in server_cfg:
            c = dataclasses.replace(cfg, base_url=url) if url else cfg
            server_cfg[key] = c
            server_auth[key] = AuthManager(c)
    cabang_cfg = {cid: server_cfg[urls.get(cid) or cfg.base_url] for cid in cabang_ids}
    cabang_auth = {cid: server_auth[urls.get(cid) or cfg.base_url] for cid in cabang_ids}

    rows = []
    d = date.fromisoformat(tgl_awal)
    end = date.fromisoformat(tgl_akhir)
    while d <= end:
        ts = d.isoformat()
        for cid in cabang_ids:
            try:
                rows.extend(audit_day(cur, cabang_cfg[cid], cabang_auth[cid], cid, ts))
            except httpx.HTTPStatusError:
                continue
        d += timedelta(days=1)
    db.close()

    totals = {}
    for r in rows:
        t = totals.setdefault((r["cabang"], r["jenis"]), {"dash": 0.0, "db": 0.0, "diff": 0.0})
        t["dash"] += r["dash"]
        t["db"] += r["db"]
        t["diff"] += r["diff"]
    return rows, totals


def main():
    cfg = Config.from_env()
    db = DatabaseManager(cfg, target_db="csb")
    db.connect()
    urls = load_cabang_urls(db)
    cabang_ids = CABANG_FILTER or sorted(urls)
    db.close()

    print(f"Rentang {TGL_AWAL}..{TGL_AKHIR}, cabang {cabang_ids}")
    rows, totals = audit_range(cabang_ids, TGL_AWAL, TGL_AKHIR)

    diff_rows = [r for r in rows if abs(r["diff"]) > 0.01]
    print()
    for (cid, jenis), t in sorted(totals.items()):
        status = "OK" if abs(t["diff"]) <= 0.01 else "DIFF"
        print(f"  cb{cid} {jenis:<32} dash={t['dash']:>15,.0f} db={t['db']:>15,.0f} d={t['diff']:>+15,.0f} [{status}]")
    print()
    if diff_rows:
        print(f"Ada {len(diff_rows)} hari yang beda (dari {len(rows)} baris audit):")
        for r in diff_rows[:30]:
            print(f"  cb{r['cabang']} {r['tanggal']} {r['jenis']:<28} dash={r['dash']:>13,.0f} db={r['db']:>13,.0f} d={r['diff']:>+13,.0f}")
        sys.exit(1)
    print("SEMUA MATCH (d=0).")


if __name__ == "__main__":
    main()
