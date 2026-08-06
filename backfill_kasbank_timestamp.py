"""Backfill created_at/updated_at/approved_at + created_by/approved_by pada kas_bank.

Sumber timestamp: `timestamp_data` di mirror (akuntansi_kasbank_masuk_detail /
akuntansi_kasbank_keluar_detail), atau di-fetch langsung dari API Brighter
per kasbank_id utk header yang tidak ada di mirror.

- created_at/updated_at dari timestamp_data (sudah UTC, waktu Brighter).
- approved_at = created_at (status 'approved' langsung saat migrasi).
- created_by/approved_by = id authenticated_users, dipetakan dari username
  Brighter (timestamp_data.created_by). Username tanpa padanan -> 'system'.

Idempoten: hanya update kas_bank yg legacy_kasbank_id-nya terisi & kolom
created_at masih NULL. Default dry-run; gunakan --apply untuk eksekusi.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import pymysql

sys.path.insert(0, os.getcwd())
from config import Config
from auth import AuthManager

# Pemetaan username Brighter (timestamp_data.created_by) -> authenticated_users.id.
# Hasil verifikasi manual (username yg ambigu diputuskan bersama user).
USERNAME_TO_ID = {
    "Jeyhan": 2802,
    "ASEP": 10671,
    "kasir": 2702,     # ADMIN KASIR
    "frengky": 2913,
    "Jehan": 2801,
    "vilia": 2892,
    "dimas": 2754,
    "rohman": 2697,
    "Putra": 2839,
    "Wisnu1": 2903,
    "Vita": 2893,
    "NINGSIH": 2912,
    "edwardo": 10914,
    "rion": 2911,
    "Kasir1": 11032,
    "NINGRUM": 2825,
    "Recky": 2846,     # RECKY SAID MARCOS
    "anton": 447,
    "yusuf": 1000481,  # user baru di authenticated_users (Brighter user_id 64)
    "Ocha": None,      # tidak ada padanan -> system
    "system": None,    # proses otomatis Brighter -> system
}


def _resolve_created_by(username):
    """None berarti 'system' (tetap 'system' di DB)."""
    if not username:
        return None
    return USERNAME_TO_ID.get(username)


def _fetch_timestamp(am: AuthManager, base_url, tipe, kasbank_id):
    """Fetch timestamp_data utk satu header dari API Brighter."""
    path = "masuk" if tipe == "masuk" else "keluar"
    url = f"{base_url}/akuntansi/kasbank_{path}/{kasbank_id}"
    try:
        r = httpx.get(url, headers=am.get_headers(), timeout=60)
        if r.status_code != 200:
            return None
        data = r.json()
        payload = data.get("data") if isinstance(data, dict) else data
        if not payload:
            return None
        return payload.get("timestamp_data")
    except Exception:
        return None


def _main():
    parser = argparse.ArgumentParser(
        description="Backfill timestamp + created_by pada kas_bank dari timestamp_data Brighter")
    parser.add_argument("--apply", action="store_true",
                        help="Eksekusi & commit. Tanpa flag ini = dry-run.")
    parser.add_argument("--max-workers", type=int, default=5,
                        help="Thread utk fetch API (default 5)")
    args = parser.parse_args()

    config = Config.from_env()
    kw = config.csb_db_kwargs()
    conn = pymysql.connect(**kw, cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4")
    cur = conn.cursor()

    am = AuthManager(config)
    am.login()

    # Semua header yg sudah dimigrasi + legacy id
    cur.execute(
        "SELECT id, tipe, legacy_kasbank_id, created_at FROM kas_bank "
        "WHERE legacy_kasbank_id IS NOT NULL ORDER BY id")
    rows = cur.fetchall()

    # timestamp_data dari mirror utk semua kasbank_id
    cur.execute("SELECT kasbank_id, timestamp_data FROM akuntansi_kasbank_keluar_detail")
    ts_keluar = {r["kasbank_id"]: r["timestamp_data"] for r in cur.fetchall()}
    cur.execute("SELECT kasbank_id, timestamp_data FROM akuntansi_kasbank_masuk_detail")
    ts_masuk = {r["kasbank_id"]: r["timestamp_data"] for r in cur.fetchall()}

    todo = []        # (kas_bank.id, tipe, kasbank_id)
    from_mirror = 0
    already = 0
    for r in rows:
        if r["created_at"] is not None:
            already += 1
            continue
        ts = ts_keluar.get(r["legacy_kasbank_id"]) if r["tipe"] == "keluar" \
            else ts_masuk.get(r["legacy_kasbank_id"])
        if ts:
            from_mirror += 1
        todo.append((r["id"], r["tipe"], r["legacy_kasbank_id"]))

    # Fetch dari API utk yg tidak ada di mirror
    need_api = [t for t in todo
                if not (ts_keluar.get(t[2]) if t[1] == "keluar" else ts_masuk.get(t[2]))]
    print("=" * 100)
    print("BACKFILL TIMESTAMP KAS_BANK | mode=%s" %
          ("APPLY (commit)" if args.apply else "DRY-RUN (no commit)"))
    print("  kas_bank migrated : %d" % len(rows))
    print("  sudah terisi      : %d" % already)
    print("  pakai mirror      : %d" % from_mirror)
    print("  perlu fetch API   : %d" % len(need_api))
    print("=" * 100)

    api_results = {}
    if need_api:
        print("Fetch timestamp_data dari API Brighter (%d header)..." % len(need_api))
        with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
            futs = {ex.submit(_fetch_timestamp, am, config.base_url, t[1], t[2]): t
                    for t in need_api}
            done = 0
            for f in as_completed(futs):
                t = futs[f]
                api_results[(t[1], t[2])] = f.result()
                done += 1
                if done % 200 == 0:
                    print("  ... %d/%d" % (done, len(need_api)))
        got = sum(1 for v in api_results.values() if v)
        print("  fetched: %d ok, %d gagal/404" % (got, len(need_api) - got))

    updates = []
    unmapped = set()
    no_ts = 0
    for kb_id, tipe, legacy in todo:
        ts = ts_keluar.get(legacy) if tipe == "keluar" else ts_masuk.get(legacy)
        if not ts:
            ts = api_results.get((tipe, legacy))
        if not ts:
            no_ts += 1
            continue
        try:
            td = json.loads(ts) if isinstance(ts, str) else (ts or {})
        except (ValueError, TypeError):
            no_ts += 1
            continue

        created_at = td.get("created_at")
        updated_at = td.get("updated_at") or created_at
        if not created_at:
            no_ts += 1
            continue
        username = td.get("created_by")
        uid = _resolve_created_by(username)
        if uid is None and username:
            unmapped.add(username)
        by = str(uid) if uid is not None else "system"

        updates.append((created_at, updated_at, created_at, by, by, kb_id))

    print("-" * 100)
    print("PLAN: %d baris akan di-update; %d tanpa timestamp (skip); username unmapped: %s" %
          (len(updates), no_ts, sorted(unmapped) or "-"))

    if args.apply:
        for created_at, updated_at, approved_at, created_by, approved_by, kb_id in updates:
            cur.execute(
                "UPDATE kas_bank SET created_at=%s, updated_at=%s, approved_at=%s, "
                "created_by=%s, approved_by=%s WHERE id=%s",
                (created_at, updated_at, approved_at, created_by, approved_by, kb_id))
        conn.commit()
        print("APPLY selesai — %d baris di-update, commit OK." % len(updates))
    else:
        print("DRY-RUN selesai — tidak ada perubahan. Jalankan dengan --apply untuk eksekusi.")

    conn.close()


if __name__ == "__main__":
    _main()
