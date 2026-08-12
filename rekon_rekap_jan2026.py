"""Rekon read-only: bandingkan brighter_penerimaan_rekap (DB) vs API Brighter
untuk rentang tanggal per cabang. Tidak menulis apa pun ke DB."""
import os
import sys
import dataclasses
from datetime import date, timedelta

sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager
from sync_saldo_kas_harian import fetch_rekap_day, load_cabang_urls, REKAP_METHODS

TGL_AWAL = sys.argv[1] if len(sys.argv) > 1 else "2026-01-01"
TGL_AKHIR = sys.argv[2] if len(sys.argv) > 2 else "2026-01-31"

default_cfg = Config.from_env()
db = DatabaseManager(default_cfg, target_db="csb")
db.connect()
cur = db.conn.cursor()

cabang_urls = load_cabang_urls(db)
print(f"Rentang: {TGL_AWAL} .. {TGL_AKHIR}")
print(f"Cabang tersedia: {sorted(cabang_urls.keys())}")

server_cfg, server_auth = {}, {}
for cid, url in cabang_urls.items():
    cfg = dataclasses.replace(default_cfg, base_url=url) if url else default_cfg
    server_cfg[cid] = cfg
    server_auth[cid] = AuthManager(cfg)

cabang_ok = []
for cid in sorted(cabang_urls):
    try:
        server_auth[cid].ensure_token()
        cabang_ok.append(cid)
    except Exception as e:
        print(f"  [skip] cabang {cid} auth gagal: {e}")

print(f"\nRekon dimulai...\n")

total_day = 0
total_match = 0
total_diff = 0
diffs = []
for cid in cabang_ok:
    print(f"--- Cabang {cid} ---")
    d = date.fromisoformat(TGL_AWAL)
    end = date.fromisoformat(TGL_AKHIR)
    while d <= end:
        total_day += 1
        ts = d.isoformat()
        cur.execute(
            "SELECT tunai, transfer, card, qris, wallet, piutang, total, synced_at "
            f"FROM `brighter_penerimaan_rekap` WHERE cabang_id=%s AND tanggal=%s",
            (cid, ts),
        )
        row = cur.fetchone()
        db_val = None
        if row:
            db_val = {
                "tunai": row["tunai"] or 0,
                "transfer": row["transfer"] or 0,
                "card": row["card"] or 0,
                "qris": row["qris"] or 0,
                "wallet": row["wallet"] or 0,
                "piutang": row["piutang"] or 0,
                "total": row["total"] or 0,
            }
        data = fetch_rekap_day(server_cfg[cid], server_auth[cid], cid, ts)
        if data:
            api = {"total": data.get("total")}
            for m in REKAP_METHODS:
                api[m] = (data.get(m) or {}).get("total_keseluruhan") or 0
        else:
            api = None
        if db_val is None and api is None:
            d += timedelta(days=1)
            continue
        if db_val is None or api is None:
            diffs.append((cid, ts, "DB_ADA" if db_val else "API_ADA", db_val, api))
            total_diff += 1
            print(f"  {ts}: {'DB ada' if db_val else 'API ada'} tapi lawannya kosong")
            d += timedelta(days=1)
            continue
        mism = {m: (db_val[m], api[m]) for m in ["tunai", "transfer", "card", "qris", "wallet", "piutang", "total"]
                if abs((db_val[m] or 0) - (api[m] or 0)) > 0.01}
        if mism:
            total_diff += 1
            diffs.append((cid, ts, mism, db_val, api))
            print(f"  {ts}: SELISIH {len(mism)} field")
            for m, (a, b) in mism.items():
                print(f"      {m:9s} DB={a:>15,.0f}  API={b:>15,.0f}  d={b-a:>+15,.0f}")
        else:
            total_match += 1
        d += timedelta(days=1)

db.close()
print("\n" + "=" * 60)
print(f"Total hari diperiksa : {total_day}")
print(f"COCOK               : {total_match}")
print(f"SELISIH             : {total_diff}")
print("=" * 60)
