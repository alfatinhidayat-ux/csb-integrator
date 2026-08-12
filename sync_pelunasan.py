"""Sync pelunasan piutang (header) per cabang dari /transaksi/pelunasan_piutang.

Endpoint ini TIDAK mendukung filter tanggal (param tanggal diabaikan), jadi sync
menarik SEMUA record per cabang (fpiutang_stat_dok=Semua) lalu di-upsert ke
brighter_transaksi_pelunasan_piutang, ditambah penghapusan record orphan (id yang
sudah tidak ada lagi di API) agar tabel selalu sejalan dengan dashboard.

Ringan: 1 endpoint, 1 tabel. Tidak menyentuh supplier/pembelian/hutang/piutang detail
(untuk itu gunakan sync_finance.py --full).

Contoh:
    python sync_pelunasan.py --env --cabang-ids 1,5
    python sync_pelunasan.py --env --cabang-ids 5 --verbose
"""

import argparse
import atexit
import dataclasses
import os
import sys

sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager
from sync_finance import (
    ENDPOINT_META,
    TABLES,
    fetch_all_pages,
    map_record,
    upsert_batch,
)
from sync_saldo_kas_harian import load_cabang_urls

TABEL = TABLES["pelunasan_piutang"]


def delete_orphans(db, cabang_id, api_ids, verbose=False):
    """Hapus record pelunasan milik cabang yang `id`-nya tidak ada lagi di API."""
    api_ids = [int(i) for i in api_ids if i is not None]
    if not api_ids:
        return 0
    try:
        cur = db.conn.cursor()
        marks = ",".join(["%s"] * len(api_ids))
        cur.execute(
            f"DELETE FROM {TABEL} WHERE cabang_id = %s AND id NOT IN ({marks})",
            (cabang_id, *api_ids),
        )
        db.conn.commit()
        return cur.rowcount
    except Exception as e:
        if verbose:
            print(f"       -> delete orphan pelunasan skipped: {e}")
        return 0


def main():
    parser = argparse.ArgumentParser(description="Sync pelunasan piutang (header) per cabang")
    parser.add_argument(
        "-e", "--env", action="store_true",
        help="Load configuration from environment variables (BRIGHTER_*)",
    )
    parser.add_argument(
        "--cabang-ids",
        help="Comma-separated cabang IDs (default: semua cabang aktif dari tabel cabang)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    default_cfg = Config.from_env()
    db = DatabaseManager(default_cfg, target_db="csb")
    db.connect()

    def _close_safe():
        try:
            db.close()
        except Exception:
            pass
    atexit.register(_close_safe)

    cabang_urls = load_cabang_urls(db)
    if args.cabang_ids:
        cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]
    else:
        cabang_ids = sorted(cabang_urls)

    server_cfg, server_auth = {}, {}
    cabang_cfg, cabang_auth = {}, {}
    for cid in cabang_ids:
        url = cabang_urls.get(cid)
        key = url or default_cfg.base_url
        if key not in server_cfg:
            cfg = dataclasses.replace(default_cfg, base_url=url) if url else default_cfg
            server_cfg[key] = cfg
            server_auth[key] = AuthManager(cfg)
        cabang_cfg[cid] = server_cfg[key]
        cabang_auth[cid] = server_auth[key]

    total = 0
    for cid in cabang_ids:
        print(f"--- Cabang {cid} ---")
        cfg_c = cabang_cfg[cid]
        auth_c = cabang_auth[cid]
        try:
            db.reconnect()
        except Exception:
            pass
        rows = fetch_all_pages(
            cfg_c, auth_c,
            ENDPOINT_META["pelunasan_piutang"]["path"], {},
            dict(ENDPOINT_META["pelunasan_piutang"]["params"]),
            cid, ENDPOINT_META["pelunasan_piutang"]["cabang_param"], args.verbose,
        )
        mapped = [map_record("pelunasan_piutang", r, cid) for r in rows]
        n = upsert_batch(db, TABEL, mapped, cid)
        total += n
        api_ids = [r.get("id") for r in mapped]
        deleted = delete_orphans(db, cid, api_ids, args.verbose)
        print(f"       -> {len(rows)} records (upsert {n}, orphan deleted {deleted})")

    db.close()
    print("=" * 50)
    print(f"PELUNASAN PIUTANG SYNC COMPLETE: {total} records")
    print("=" * 50)


if __name__ == "__main__":
    main()
