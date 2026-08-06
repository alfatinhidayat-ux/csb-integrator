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

Kebijakan status dokumen:
  * PERHITUNGAN REKON hanya memakai status 'Tertutup' (dokumen sah).
  * Data 'Batal' tetap DITAMPILKAN (kolom *_btl) sebagai referensi
    historical tetapi TIDAK dijumlah ke angka selisih rekon.
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

    print("=" * 130)
    print(f"{'Cbg':>3} {'ln_tt':>6} {'nil_tt':>14} {'pln_tt':>14} {'sis_tt':>14} | "
          f"{'dsb_tt':>14} {'ccl_tt':>14} {'slsh_nil':>13} {'slsh_sis':>13} | "
          f"{'ln_btl':>6} {'nil_btl':>14} {'pln_btl':>14} {'sis_btl':>14}")
    print("=" * 130)
    totals = {"loan_tt": 0, "nilai_tt": 0, "pelunasan_tt": 0, "sisa_tt": 0,
              "disburse_tt": 0, "cicilan_tt": 0,
              "loan_btl": 0, "nilai_btl": 0, "pelunasan_btl": 0, "sisa_btl": 0}
    for c in cabangs:
        cid = c["id"]
        p = one(f"SELECT COUNT(*) n, COALESCE(SUM(ppinjaman_nilai),0) v, "
                f"COALESCE(SUM(ppinjaman_pelunasan),0) p, COALESCE(SUM(ppinjaman_sisa),0) s "
                f"FROM pinjaman_karyawan WHERE ppinjaman_status='Tertutup' "
                f"AND ppinjaman_cabang_id={cid}")
        d = one(f"SELECT COALESCE(SUM(kasbank_pengeluaran_pinjaman_karyawan),0) v "
                f"FROM akuntansi_kasbank_keluar WHERE kasbank_pengeluaran_pinjaman_karyawan>0 "
                f"AND kasbank_cabang_id={cid}")
        m = one(f"SELECT COALESCE(SUM(CAST(k.kdpk_dilunasi AS DECIMAL(15,2))),0) v "
                f"FROM akuntansi_kasbank_masuk_piutang_karyawan k "
                f"JOIN akuntansi_kasbank_masuk h ON h.kasbank_id=k.kdpk_master_id "
                f"WHERE h.kasbank_cabang_id={cid}")
        pb = one(f"SELECT COUNT(*) n, COALESCE(SUM(ppinjaman_nilai),0) v, "
                 f"COALESCE(SUM(ppinjaman_pelunasan),0) p, COALESCE(SUM(ppinjaman_sisa),0) s "
                 f"FROM pinjaman_karyawan WHERE ppinjaman_status='Batal' "
                 f"AND ppinjaman_cabang_id={cid}")
        loan_tt, nilai_tt, pelunasan_tt, sisa_tt = p["n"], _dec(p["v"]), _dec(p["p"]), _dec(p["s"])
        disburse_tt, cicilan_tt = _dec(d["v"]), _dec(m["v"])
        loan_btl, nilai_btl, pelunasan_btl, sisa_btl = pb["n"], _dec(pb["v"]), _dec(pb["p"]), _dec(pb["s"])
        slsh_nilai = disburse_tt - nilai_tt
        slsh_sisa = (sisa_tt - (nilai_tt - pelunasan_tt))
        print(f"{cid:>3} {loan_tt:>6} {nilai_tt:>14,.0f} {pelunasan_tt:>14,.0f} {sisa_tt:>14,.0f} | "
              f"{disburse_tt:>14,.0f} {cicilan_tt:>14,.0f} {slsh_nilai:>13,.0f} {slsh_sisa:>13,.0f} | "
              f"{loan_btl:>6} {nilai_btl:>14,.0f} {pelunasan_btl:>14,.0f} {sisa_btl:>14,.0f}")
        for k in totals:
            totals[k] += {"loan_tt": loan_tt, "nilai_tt": nilai_tt, "pelunasan_tt": pelunasan_tt,
                          "sisa_tt": sisa_tt, "disburse_tt": disburse_tt, "cicilan_tt": cicilan_tt,
                          "loan_btl": loan_btl, "nilai_btl": nilai_btl,
                          "pelunasan_btl": pelunasan_btl, "sisa_btl": sisa_btl}[k]
    print("=" * 130)
    t = totals
    print(f"{'TOT':>3} {t['loan_tt']:>6} {t['nilai_tt']:>14,.0f} {t['pelunasan_tt']:>14,.0f} {t['sisa_tt']:>14,.0f} | "
          f"{t['disburse_tt']:>14,.0f} {t['cicilan_tt']:>14,.0f} "
          f"{t['disburse_tt']-t['nilai_tt']:>13,.0f} "
          f"{t['sisa_tt']-(t['nilai_tt']-t['pelunasan_tt']):>13,.0f} | "
          f"{t['loan_btl']:>6} {t['nilai_btl']:>14,.0f} {t['pelunasan_btl']:>14,.0f} {t['sisa_btl']:>14,.0f}")
    print()

    print("-- Cek 1: loan Tertutup sisa != nilai - pelunasan (inkonsistensi internal) --")
    bad = one(f"SELECT COUNT(*) n FROM pinjaman_karyawan "
              f"WHERE ppinjaman_status='Tertutup' "
              f"AND ABS(ppinjaman_sisa - (ppinjaman_nilai - ppinjaman_pelunasan)) > {TOLERANCE}")
    print(f"  -> {bad['n']} loan tidak konsisten")
    if bad["n"]:
        cur.execute(f"SELECT ppinjaman_id,ppinjaman_cabang_id,ppinjaman_nilai,ppinjaman_pelunasan,ppinjaman_sisa "
                    f"FROM pinjaman_karyawan "
                    f"WHERE ppinjaman_status='Tertutup' "
                    f"AND ABS(ppinjaman_sisa - (ppinjaman_nilai - ppinjaman_pelunasan)) > {TOLERANCE} LIMIT 10")
        for r in cur.fetchall():
            print(f"     {r['ppinjaman_id']} cbg {r['ppinjaman_cabang_id']}: "
                  f"nilai={r['ppinjaman_nilai']} pelunasan={r['ppinjaman_pelunasan']} sisa={r['ppinjaman_sisa']}")

    print("-- Cek 2: pembayaran di kasbank yang loan-nya tidak ada di pinjaman_karyawan --")
    orphan = one(f"SELECT COUNT(DISTINCT kdpk_ppinjaman_id) n "
                 f"FROM akuntansi_kasbank_masuk_piutang_karyawan k "
                 f"LEFT JOIN pinjaman_karyawan p ON p.ppinjaman_id=k.kdpk_ppinjaman_id "
                 f"WHERE p.ppinjaman_id IS NULL")
    print(f"  -> {orphan['n']} loan orphan (berbahaya bila > 0)")

    print("-- Cek 3: overpayment (kdpk_dilunasi > nilai pinjaman Tertutup) --")
    cur.execute(f"SELECT k.kdpk_ppinjaman_id, k.kdpk_dilunasi, p.ppinjaman_nilai "
                f"FROM akuntansi_kasbank_masuk_piutang_karyawan k "
                f"JOIN pinjaman_karyawan p ON p.ppinjaman_id=k.kdpk_ppinjaman_id "
                f"WHERE p.ppinjaman_status='Tertutup' "
                f"AND CAST(k.kdpk_dilunasi AS DECIMAL(15,2)) > p.ppinjaman_nilai + {TOLERANCE} LIMIT 10")
    rows = cur.fetchall()
    print(f"  -> {len(rows)} pembayaran melebihi nilai pinjaman (contoh 10):")
    for r in rows:
        print(f"     loan {r['kdpk_ppinjaman_id']}: dibayar {r['kdpk_dilunasi']} vs nilai {r['ppinjaman_nilai']}")

    print("-- Info Batal (historical, TIDAK masuk perhitungan rekon) --")
    btl = one(f"SELECT COUNT(*) n, COALESCE(SUM(ppinjaman_nilai),0) v "
              f"FROM pinjaman_karyawan WHERE ppinjaman_status='Batal'")
    print(f"  -> {btl['n']} loan Batal (nilai {btl['v']:,.0f})")

    conn.close()
    print("\nKeterangan kolom (*_tt = rekon, Tertutup saja): loan/nilai/pelunasan/sisa dari "
          "pinjaman_karyawan, disburse = pengeluaran pinjaman karyawan dari kasbank_keluar, "
          "cicilan = pelunasan piutang karyawan dari kasbank_masuk. slsh_nilai = disburse - nilai "
          "(perbedaan normal karena ada pinjaman yang belum/tidak lewat kasbank). "
          "Kolom *_btl = info Batal (historical, tidak dihitung di rekon).")


if __name__ == "__main__":
    main()