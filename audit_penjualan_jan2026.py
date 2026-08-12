"""Audit per hari: bandingkan penjualan DB (pos_transactions) vs dashboard Brighter
(rekap_dashboard API). Rumus net sales dashboard = paid + piutang - retur.

db_gross dihitung dari pos_transactions hanya utk payment_status 'paid'/'partial'
(analog pos_ok status 'Tertutup'; 'open'/'cancelled' tidak dihitung dashboard) dan
meng-exclude payment_method='piutang' (invoice modul piutang masuk jenis
pelunasan_piutang_penjualan, bukan penjualan)."""
import os
import sys
import dataclasses
from datetime import date, timedelta

sys.path.insert(0, os.getcwd())

import httpx
from config import Config
from auth import AuthManager
from db import DatabaseManager
from sync_saldo_kas_harian import load_cabang_urls

TGL_AWAL = sys.argv[1] if len(sys.argv) > 1 else "2026-01-01"
TGL_AKHIR = sys.argv[2] if len(sys.argv) > 2 else "2026-01-31"

cfg = Config.from_env()
db = DatabaseManager(cfg, target_db="csb")
db.connect()
cur = db.conn.cursor()
urls = load_cabang_urls(db)

auths = {}
for cid, url in urls.items():
    c = dataclasses.replace(cfg, base_url=url)
    auths[cid] = (c, AuthManager(c))

def fetch_daily(cid, ts):
    c, a = auths[cid]
    a.ensure_token()
    params = {"tanggal_awal": ts, "tanggal_akhir": ts, "cabang_id": str(cid)}
    r = httpx.get(c.base_url + "/laporan/dashboard/rekap_dashboard", params=params,
                  headers=a.get_headers(), timeout=60)
    if r.status_code == 404:
        return {"dash_paid": 0, "dash_piutang": 0, "dash_gross": 0, "dash_retur": 0}
    r.raise_for_status()
    data = r.json().get("data") or []
    out = {}
    for b in data:
        out[b["jenis_dashboard"]] = b["rekap"]
    pen = out.get("penjualan") or {}
    ret = out.get("retur_jual") or {}
    return {
        "dash_paid": (pen.get("total_rp") or 0),
        "dash_piutang": (pen.get("total_piutang_rp") or 0),
        "dash_gross": (pen.get("total_rp") or 0) + (pen.get("total_piutang_rp") or 0),
        "dash_retur": (ret.get("total_rp") or 0),
    }

targets = [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else sorted(urls)
targets = [t for t in targets if t in urls]

print(f"Rentang {TGL_AWAL}..{TGL_AKHIR}, cabang {targets}")
print()

for cid in targets:
    print(f"===== CABANG {cid} =====")
    d = date.fromisoformat(TGL_AWAL)
    end = date.fromisoformat(TGL_AKHIR)
    tot_dash_net = 0
    tot_our_net = 0
    tot_dash_retur = 0
    tot_our_retur = 0
    n_diff = 0
    while d <= end:
        ts = d.isoformat()
        cur.execute(
            "SELECT SUM(total_amount) gross, COUNT(*) n FROM pos_transactions "
            "WHERE cabang_id=%s AND DATE(waktu_transaksi)=%s "
            "AND payment_status IN ('paid','partial') AND payment_method<>'piutang'",
            (cid, ts))
        row = cur.fetchone()
        our_gross = float(row["gross"] or 0)
        cur.execute(
            "SELECT IFNULL(SUM(total_rp),0) retur FROM brighter_retur_penjualan "
            "WHERE cabang_id=%s AND tanggal=%s AND status_dokumen='Tertutup'",
            (cid, ts))
        our_retur = float(cur.fetchone()["retur"] or 0)
        dash = fetch_daily(cid, ts)
        dash_net = dash["dash_gross"] - dash["dash_retur"]
        our_net = our_gross - our_retur
        tot_dash_net += dash_net
        tot_our_net += our_net
        tot_dash_retur += dash["dash_retur"]
        tot_our_retur += our_retur
        diff = our_net - dash_net
        if abs(diff) > 0.01:
            n_diff += 1
            print(f"  {ts}: dash_gross={dash['dash_gross']:>13,.0f} retur={dash['dash_retur']:>10,.0f} net={dash_net:>13,.0f} | "
                  f"db_gross={our_gross:>13,.0f} retur={our_retur:>10,.0f} net={our_net:>13,.0f} | d={diff:>+13,.0f}")
        d += timedelta(days=1)
    print(f"  TOTAL: dash_net={tot_dash_net:,.0f}  db_net={tot_our_net:,.0f}  d={tot_our_net-tot_dash_net:+,.0f} | "
          f"retur dash={tot_dash_retur:,.0f} db={tot_our_retur:,.0f} (hari berbeda={n_diff})")
    print()

db.close()
