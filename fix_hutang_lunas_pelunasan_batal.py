"""Koreksi faktur pembelian yang statusnya Lunas tapi pelunasannya Batal.

Latar belakang (kasus PT. GUNUNG AGUNG SENTOSA, cabang 5, total 278,832,516):
faktur pembelian berstatus `Tertutup` + `Lunas` tetapi nilai pelunasan yang
mereferensikannya (`brighter_transaksi_pelunasan_hutang`) berstatus `Batal`.
Akibatnya di staging: `total_bayar_rp = 0`, `total_sisa_rp = total_net_rp`
(padahal faktur sudah lunas), sehingga rekon sisa hutang TIDAK cocok dengan
laporan resmi PDF (PDF mengecualikan faktur yang pelunasannya Batal).

Script ini menormalkan faktur tsb menjadi `total_bayar_rp = total_net_rp`,
`total_sisa_rp = 0`.

Jalankan SETELAH `sync_finance.py` (yang menimpa staging dari API):
    python fix_hutang_lunas_pelunasan_batal.py            # dry-run
    python fix_hutang_lunas_pelunasan_batal.py --apply    # eksekusi & commit

Idempoten: hanya memproses faktur yang masih `sisa > 0`, jadi aman
dijalankan berulang-ulang.
"""
import argparse
import os
import sys

import pymysql

sys.path.insert(0, os.getcwd())
from config import Config

SUPPLIER_FILTER = "PT. GUNUNG AGUNG SENTOSA"


def _target_faktur(cur, limit=None):
    """Faktur Tertutup+Lunas yang pelunasannya (masih aktif) semua Batal.

    Kriteria:
      - p.status_dok = 'Tertutup' AND p.status_lunas = 'Lunas'
      - p.total_sisa_rp > 0 (belum dinormalisasi)
      - SEMUA pelunasan yang mereferensikannya berstat_dok 'Batal'
        (tidak ada pelunasan Tertutup yang valid).
    """
    sql = """
        SELECT p.id, p.cabang_id, p.nobukti,
               ROUND(p.total_net_rp)  total_net_rp,
               ROUND(p.total_bayar_rp) total_bayar_rp,
               ROUND(p.total_sisa_rp) total_sisa_rp,
               GROUP_CONCAT(DISTINCT h.nobukti ORDER BY h.nobukti SEPARATOR ', ') no_pelunasan
        FROM brighter_persediaan_pembelian p
        JOIN brighter_transaksi_pelunasan_hutang_detail d
             ON d.master_hutang_data_pembelian_id = p.id
        JOIN brighter_transaksi_pelunasan_hutang h ON h.id = d.master_lunas_id
        WHERE p.status_dok = 'Tertutup'
          AND p.status_lunas = 'Lunas'
          AND COALESCE(p.total_sisa_rp, 0) > 0.5
          AND p.supplier_data_supplier_nama = %s
        GROUP BY p.id, p.cabang_id, p.nobukti,
                 p.total_net_rp, p.total_bayar_rp, p.total_sisa_rp
        HAVING COUNT(DISTINCT CASE WHEN h.stat_dok = 'Tertutup' THEN h.id END) = 0
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    cur.execute(sql, (SUPPLIER_FILTER,))
    return cur.fetchall()


def _main():
    parser = argparse.ArgumentParser(
        description="Normalisasi faktur Lunas yang pelunasannya Batal (bayar=net, sisa=0)")
    parser.add_argument("--apply", action="store_true",
                        help="Eksekusi & commit. Tanpa flag ini = dry-run.")
    parser.add_argument("--supplier", default=SUPPLIER_FILTER,
                        help=f"Filter supplier (default: {SUPPLIER_FILTER})")
    parser.add_argument("--limit", type=int, default=None,
                        help="Batasi jumlah baris yang diproses (debug)")
    args = parser.parse_args()

    config = Config.from_env()
    conn = pymysql.connect(**config.csb_db_kwargs(),
                           cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4")
    cur = conn.cursor()
    try:
        targets = _target_faktur(cur, args.limit)
        total = sum(r["total_sisa_rp"] for r in targets)
        print("=" * 100)
        print("FIX HUTANG LUNAS - PELUNASAN BATAL | mode=%s" %
              ("APPLY (commit)" if args.apply else "DRY-RUN (no commit)"))
        print("  supplier        : %s" % args.supplier)
        print("  faktur ditemukan: %d" % len(targets))
        print("  total koreksi   : %s" % f"{total:,}".replace(",", "."))
        print("=" * 100)
        for r in targets:
            print("  cbg %-3s %-20s net=%12s byr=%12s sisa=%12s  <- %s" % (
                r["cabang_id"], r["nobukti"],
                f"{r['total_net_rp']:,}", f"{r['total_bayar_rp']:,}",
                f"{r['total_sisa_rp']:,}", r["no_pelunasan"] or "-"))
        if not targets:
            print("Tidak ada faktur yang perlu dikoreksi (sudah bersih).")
            return

        if args.apply:
            n = cur.executemany(
                "UPDATE brighter_persediaan_pembelian "
                "SET total_bayar_rp = total_net_rp, total_sisa_rp = 0 "
                "WHERE id = %s",
                [(r["id"],) for r in targets])
            conn.commit()
            print("-" * 100)
            print("APPLY selesai - %d faktur di-update, commit OK." % n)
        else:
            print("-" * 100)
            print("DRY-RUN selesai - tidak ada perubahan. "
                  "Jalankan dengan --apply untuk eksekusi.")
    finally:
        conn.close()


if __name__ == "__main__":
    _main()
