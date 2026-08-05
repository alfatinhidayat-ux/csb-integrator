"""Rekonsiliasi pinjaman karyawan vs tabel akuntansi kas/bank.

Membandingkan (per cabang) agar saling nyambung:
- pinjaman_karyawan            : nilai, pelunasan, sisa
- akuntansi_kasbank_keluar     : disbursement pinjaman karyawan
- akuntansi_kasbank_masuk      : cicilan piutang karyawan (detail_piutang_karyawan)

Script ini READ-ONLY: tidak mengubah/menghapus data apa pun.
Untuk memperbaiki data, jalankan dulu:
    python main.py --env --kasbank-only     # clear + re-sync kasbank
    python sync_pinjaman.py --env           # re-sync pinjaman_karyawan
lalu jalankan ulang script ini.
"""
import argparse
import os
import sys

import pymysql

sys.path.insert(0, os.getcwd())
from config import Config

TOLERANCE = 0.5  # rupiah (pembulatan)


def _dec(v):
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def main():
    parser = argparse.ArgumentParser(description="Rekonsiliasi pinjaman karyawan vs kas/bank")
    parser.add_argument("-e", "--env", action="store_true")
    parser.add_argument("--cabang-ids", default=None,
                        help="Comma-separated cabang IDs; default semua")
    args = parser.parse_args()

    config = Config.from_env()
    kw = config.csb_db_kwargs()
    conn = pymysql.connect(**kw, cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4")
    cur = conn.cursor()

    cabang_filter = ""
    cabang_ids = []
    if args.cabang_ids:
        cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]
        cabang_filter = "AND id IN (%s)" % ",".join(str(x) for x in cabang_ids)

    cur.execute(f"SELECT id, nama, aktif FROM cabang WHERE aktif=1 {cabang_filter} ORDER BY id")
    cabangs = cur.fetchall()

    def one(sql):
        cur.execute(sql)
        row = cur.fetchone()
        return row or {}

    print("=" * 100)
    print(f"{'Cbg':>3} {'loan':>6} {'nilai':>15} {'pelunasan':>15} {'sisa':>15} | "
          f"{'disburse':>15} {'cicilan':>15} {'slsh_nilai':>14} {'slsh_sisa':>14}")
    print("=" * 100)
    totals = {"loan": 0, "nilai": 0, "pelunasan": 0, "sisa": 0,
              "disburse": 0, "cicilan": 0}
    for c in cabangs:
        cid = c["id"]
        p = one(f"SELECT COUNT(*) n, COALESCE(SUM(ppinjaman_nilai),0) v, "
                f"COALESCE(SUM(ppinjaman_pelunasan),0) p, COALESCE(SUM(ppinjaman_sisa),0) s "
                f"FROM pinjaman_karyawan WHERE ppinjaman_cabang_id={cid}")
        d = one(f"SELECT COALESCE(SUM(kasbank_pengeluaran_pinjaman_karyawan),0) v "
                f"FROM akuntansi_kasbank_keluar WHERE kasbank_pengeluaran_pinjaman_karyawan>0 "
                f"AND kasbank_cabang_id={cid}")
        m = one(f"SELECT COALESCE(SUM(CAST(k.kdpk_dilunasi AS DECIMAL(15,2))),0) v "
                f"FROM akuntansi_kasbank_masuk_piutang_karyawan k "
                f"JOIN akuntansi_kasbank_masuk h ON h.kasbank_id=k.kdpk_master_id "
                f"WHERE h.kasbank_cabang_id={cid}")
        loan, nilai, pelunasan, sisa = p["n"], _dec(p["v"]), _dec(p["p"]), _dec(p["s"])
        disburse, cicilan = _dec(d["v"]), _dec(m["v"])
        slsh_nilai = disburse - nilai
        slsh_sisa = (sisa - (nilai - pelunasan))
        print(f"{cid:>3} {loan:>6} {nilai:>15,.0f} {pelunasan:>15,.0f} {sisa:>15,.0f} | "
              f"{disburse:>15,.0f} {cicilan:>15,.0f} {slsh_nilai:>14,.0f} {slsh_sisa:>14,.0f}")
        for k in totals:
            totals[k] += {"loan": loan, "nilai": nilai, "pelunasan": pelunasan,
                          "sisa": sisa, "disburse": disburse, "cicilan": cicilan}[k]
    print("=" * 100)
    t = totals
    print(f"{'TOT':>3} {t['loan']:>6} {t['nilai']:>15,.0f} {t['pelunasan']:>15,.0f} {t['sisa']:>15,.0f} | "
          f"{t['disburse']:>15,.0f} {t['cicilan']:>15,.0f} "
          f"{t['disburse']-t['nilai']:>14,.0f} "
          f"{t['sisa']-(t['nilai']-t['pelunasan']):>14,.0f}")
    print()

    print("-- Cek 1: sisa != nilai - pelunasan (inkonsistensi internal) --")
    bad = one(f"SELECT COUNT(*) n FROM pinjaman_karyawan "
              f"WHERE ABS(ppinjaman_sisa - (ppinjaman_nilai - ppinjaman_pelunasan)) > {TOLERANCE}")
    print(f"  -> {bad['n']} loan tidak konsisten")
    if bad["n"]:
        cur.execute(f"SELECT ppinjaman_id,ppinjaman_cabang_id,ppinjaman_nilai,ppinjaman_pelunasan,ppinjaman_sisa "
                    f"FROM pinjaman_karyawan "
                    f"WHERE ABS(ppinjaman_sisa - (ppinjaman_nilai - ppinjaman_pelunasan)) > {TOLERANCE} LIMIT 10")
        for r in cur.fetchall():
            print(f"     {r['ppinjaman_id']} cbg {r['ppinjaman_cabang_id']}: "
                  f"nilai={r['ppinjaman_nilai']} pelunasan={r['ppinjaman_pelunasan']} sisa={r['ppinjaman_sisa']}")

    print("-- Cek 2: pembayaran di kasbank yang loan-nya tidak ada di pinjaman_karyawan --")
    orphan = one(f"SELECT COUNT(DISTINCT kdpk_ppinjaman_id) n "
                 f"FROM akuntansi_kasbank_masuk_piutang_karyawan k "
                 f"LEFT JOIN pinjaman_karyawan p ON p.ppinjaman_id=k.kdpk_ppinjaman_id "
                 f"WHERE p.ppinjaman_id IS NULL")
    print(f"  -> {orphan['n']} loan orphan (berbahaya bila > 0)")

    print("-- Cek 3: overpayment (kdpk_dilunasi > nilai pinjaman) --")
    cur.execute(f"SELECT k.kdpk_ppinjaman_id, k.kdpk_dilunasi, p.ppinjaman_nilai "
                f"FROM akuntansi_kasbank_masuk_piutang_karyawan k "
                f"JOIN pinjaman_karyawan p ON p.ppinjaman_id=k.kdpk_ppinjaman_id "
                f"WHERE CAST(k.kdpk_dilunasi AS DECIMAL(15,2)) > p.ppinjaman_nilai + {TOLERANCE} LIMIT 10")
    rows = cur.fetchall()
    print(f"  -> {len(rows)} pembayaran melebihi nilai pinjaman (contoh 10):")
    for r in rows:
        print(f"     loan {r['kdpk_ppinjaman_id']}: dibayar {r['kdpk_dilunasi']} vs nilai {r['ppinjaman_nilai']}")

    conn.close()
    print("\nKeterangan kolom: disburse = pengeluaran pinjaman karyawan dari kasbank_keluar, "
          "cicilan = pelunasan piutang karyawan dari kasbank_masuk. slsh_nilai = disburse - nilai "
          "(perbedaan normal karena ada pinjaman yang belum/tidak lewat kasbank).")


if __name__ == "__main__":
    main()