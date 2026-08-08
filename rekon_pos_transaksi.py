"""Rekonsiliasi POS: pos_ok (acuan/excel) vs pos_transactions (+ items).

Membandingkan (per cabang) data penjualan POS antara:
- Acuan: pos_ok + pos_ok_detail  <- hasil import Excel "Penjualan_POS_Brighter_per_Cabang.xlsx"
  (sheet Ringkasan di Excel = agregasi tabel ini, tanpa filter status)
- Pos  : pos_transactions + pos_transaction_items (hasil migrasi POS baru)

Hanya record pos_transactions yang punya legacy_id (migrasi dari brighter_pos)
yang ikut dihitung; record dengan legacy_id NULL (entri langsung di app baru /
belum migrasi) TIDAK masuk perhitungan.

Kebijakan:
  * TIDAK ada filter status dokumen (acuan Ringkasan juga tanpa filter);
  * Rentang default 10/01/2026 - 31/07/2026 (sama seperti sheet Ringkasan);
  * Kolom kanal (Tunai/Qris/Transfer/Kartu/KanLain) hanya dari acuan, karena
    pos_transactions tidak menyimpan rincian kanal (hanya payment_method).

Script ini READ-ONLY: tidak mengubah/menghapus data apa pun.
"""
import argparse
import os
import sys

import pymysql

sys.path.insert(0, os.getcwd())
from config import Config

ZERO_ACUAN = {"jml": 0, "total_biaya": 0.0, "tunai": 0.0, "qris": 0.0, "transfer": 0.0,
              "kartu_edc": 0.0, "kanal_lain": 0.0, "total_bayar": 0.0, "selisih": 0.0, "baris": 0}
ZERO_POS = {"jml": 0, "total": 0.0, "total_bayar": 0.0, "baris": 0}
TOLERANCE = 0.5


def curv(v):
    return 0.0 if v is None else float(v)


def main():
    parser = argparse.ArgumentParser(description="Rekonsiliasi pos_ok (acuan) vs pos_transaksi")
    parser.add_argument("-e", "--env", action="store_true")
    parser.add_argument("--tanggal-awal", default="2026-01-10")
    parser.add_argument("--tanggal-akhir", default="2026-07-31")
    args = parser.parse_args()

    config = Config.from_env()
    kw = config.csb_db_kwargs()
    conn = pymysql.connect(**kw, cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4")
    cur = conn.cursor()

    tgl_awal = args.tanggal_awal
    tgl_akhir = args.tanggal_akhir

    cur.execute("SELECT id, nama FROM cabang")
    cbg_name = {r["id"]: r["nama"] for r in cur.fetchall()}

    def fetch_map(sql):
        cur.execute(sql)
        return {r["cabang_id"]: r for r in cur.fetchall()}

    # ---- Acuan: agregat pos_ok (semua status, rentang tanggal) ----
    acuan = fetch_map(f"""
        SELECT cabang_id,
               COUNT(*)                         AS jml,
               SUM(COALESCE(total_biaya,0))     AS total_biaya,
               SUM(COALESCE(tunai,0))           AS tunai,
               SUM(COALESCE(qris_barcode,0))    AS qris,
               SUM(COALESCE(transfer,0))        AS transfer,
               SUM(COALESCE(kartu_edc_kanal,0)) AS kartu_edc,
               SUM(COALESCE(kanal_lain,0))      AS kanal_lain,
               SUM(COALESCE(total_bayar,0))     AS total_bayar,
               SUM(COALESCE(selisih,0))         AS selisih
        FROM pos_ok
        WHERE tanggal BETWEEN '{tgl_awal}' AND '{tgl_akhir}'
        GROUP BY cabang_id
    """)
    acuan_baris = fetch_map(f"""
        SELECT cabang_id, COUNT(*) AS baris
        FROM pos_ok_detail
        WHERE tanggal BETWEEN '{tgl_awal}' AND '{tgl_akhir}'
        GROUP BY cabang_id
    """)

    def _norm(r):
        for k, v in r.items():
            if isinstance(v, int):
                r[k] = float(v)
            elif hasattr(v, "__float__"):
                r[k] = float(v)
        return r


    for c, r in acuan.items():
        _norm(r)
        r["baris"] = acuan_baris.get(c, {}).get("baris", 0)
    for c, r in acuan_baris.items():
        r["baris"] = float(r["baris"])

    # ---- Pos: header + detail, hanya legacy_id NOT NULL ----
    pos = fetch_map(f"""
        SELECT cabang_id, COUNT(*)                   AS jml,
               SUM(COALESCE(total_amount,0))        AS total,
               SUM(COALESCE(paid_amount,0))         AS total_bayar
        FROM pos_transactions
        WHERE legacy_id IS NOT NULL
          AND DATE(waktu_transaksi) BETWEEN '{tgl_awal}' AND '{tgl_akhir}'
        GROUP BY cabang_id
    """)
    pos_baris = fetch_map(f"""
        SELECT p.cabang_id, COUNT(*) AS baris
        FROM pos_transaction_items i
        JOIN pos_transactions p ON p.id = i.pos_transaction_id
        WHERE p.legacy_id IS NOT NULL
          AND DATE(p.waktu_transaksi) BETWEEN '{tgl_awal}' AND '{tgl_akhir}'
        GROUP BY p.cabang_id
    """)
    for c, r in pos.items():
        _norm(r)
        r["baris"] = pos_baris.get(c, {}).get("baris", 0)
    for c, r in pos_baris.items():
        r["baris"] = float(r["baris"])

    all_ids = sorted(set(acuan) | set(pos))

    # ---- Cetak ----
    head = (f"{'':>4} {'':>10} {'Acuan (pos_ok)':^66} | {'Pos (legacy)':^40} | Selisih (Acuan-Pos)")
    sep = "=" * 132
    print(sep)
    print(head)
    print(sep)
    print(f"{'Cbg':>4} {'Cabang':>10} {'Jml':>7} {'TotalBiaya':>14} {'Tunai':>13} {'Qris':>12} "
          f"{'Transf':>12} {'Kartu':>11} {'KanLain':>11} {'TotalByr':>13} {'Slsh':>10} {'Brs':>7} "
          f"{'Jml':>7} {'Total':>15} {'Byr':>13} {'Brs':>6} "
          f"| {'dJml':>7} {'dTotal':>15} {'dByr':>13} {'dBrs':>6}")

    tot_a = {"jml": 0, "total_biaya": 0.0, "tunai": 0.0, "qris": 0.0, "transfer": 0.0,
             "kartu_edc": 0.0, "kanal_lain": 0.0, "total_bayar": 0.0, "selisih": 0.0, "baris": 0}
    tot_p = {"jml": 0, "total": 0.0, "total_bayar": 0.0, "baris": 0}

    for c in all_ids:
        a = {**ZERO_ACUAN, **acuan.get(c, {})}
        p = {**ZERO_POS, **pos.get(c, {})}
        nm = cbg_name.get(c, f"CBANG {c}")
        dj = int(a["jml"]) - int(p["jml"])
        dt = curv(a["total_biaya"]) - curv(p["total"])
        dbyr = curv(a["total_bayar"]) - curv(p["total_bayar"])
        dbr = int(a["baris"]) - int(p["baris"])
        print(f"{c:>4} {nm:>10} {int(a['jml']):>7} {curv(a['total_biaya']):>14,.0f} "
              f"{curv(a['tunai']):>13,.0f} {curv(a['qris']):>12,.0f} "
              f"{curv(a['transfer']):>12,.0f} {curv(a['kartu_edc']):>10,.0f} "
              f"{curv(a['kanal_lain']):>11,.0f} {curv(a['total_bayar']):>11,.0f} "
              f"{curv(a['selisih']):>10,.0f} {int(a['baris']):>7} | "
              f"{int(p['jml']):>7} {curv(p['total']):>14,.0f} {curv(p['total_bayar']):>13,.0f} "
              f"{int(p['baris']):>6} | {dj:>7} {dt:>15,.0f} {dbyr:>13,.0f} {dbr:>6}")

        for k in tot_a:
            tot_a[k] += a[k]
        for k in tot_p:
            tot_p[k] += p[k]

    # Total
    dj = int(tot_a["jml"]) - int(tot_p["jml"])
    dt = curv(tot_a["total_biaya"]) - curv(tot_p["total"])
    dbyr = curv(tot_a["total_bayar"]) - curv(tot_p["total_bayar"])
    dbr = int(tot_a["baris"]) - int(tot_p["baris"])
    print(sep)
    print(f"{'':>4} {'TOTAL':>10} {int(tot_a['jml']):>7} {curv(tot_a['total_biaya']):>14,.0f} "
          f"{curv(tot_a['tunai']):>13,.0f} {curv(tot_a['qris']):>12,.0f} "
          f"{curv(tot_a['transfer']):>12,.0f} {curv(tot_a['kartu_edc']):>10,.0f} "
          f"{curv(tot_a['kanal_lain']):>11,.0f} {curv(tot_a['total_bayar']):>11,.0f} "
          f"{curv(tot_a['selisih']):>10,.0f} {int(tot_a['baris']):>7} | "
          f"{int(tot_p['jml']):>7} {curv(tot_p['total']):>14,.0f} {curv(tot_p['total_bayar']):>13,.0f} "
          f"{int(tot_p['baris']):>6} | {dj:>7} {dt:>15,.0f} {dbyr:>13,.0f} {dbr:>6}")
    print()

    # ---- Ringkasan perbedaan ----
    print("-- Selisih per cabang (hanya yang tidak cocok) --")
    found = False
    for c in all_ids:
        a = {**ZERO_ACUAN, **acuan.get(c, {})}
        p = {**ZERO_POS, **pos.get(c, {})}
        dj = int(a["jml"]) - int(p["jml"])
        dt = curv(a["total_biaya"]) - curv(p["total"])
        dbyr = curv(a["total_bayar"]) - curv(p["total_bayar"])
        dbr = int(a["baris"]) - int(p["baris"])
        if dj or abs(dt) > 0.5 or abs(dbyr) > 0.5 or dbr:
            found = True
            nm = cbg_name.get(c, f"CB{c}")
            print(f"  {nm} (cbg {c}): dJml={dj:+d} dTotal={dt:+,.0f} dByr={dbyr:+,.0f} "
                  f"dBrs={dbr:+d}")
    if not found:
        print("  SEMUA COCOK")

    conn.close()


if __name__ == "__main__":
    main()