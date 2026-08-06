"""Rekonsiliasi pembelian (hutang) vs pelunasan hutang.

Membandingkan (per cabang) agar saling nyambung:
- persediaan_pembelian          : net, bayar, sisa per faktur
- transaksi_pelunasan_hutang    : header pelunasan hutang
- transaksi_pelunasan_hutang_detail : detail pelunasan (link ke faktur pembelian)

Script ini READ-ONLY: tidak mengubah/menghapus data apa pun.
Untuk memperbaiki data, jalankan dulu:
    python sync_finance.py --env      # re-sync pembelian & pelunasan hutang
lalu jalankan ulang script ini.

Catatan: header pelunasan memakai kolom `stat_dok` (bukan `status_dok`).

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
    parser = argparse.ArgumentParser(description="Rekonsiliasi pembelian vs pelunasan hutang")
    parser.add_argument("-e", "--env", action="store_true")
    parser.add_argument("--cabang-ids", default=None,
                        help="Comma-separated cabang IDs; default semua")
    args = parser.parse_args()

    config = Config.from_env()
    kw = config.csb_db_kwargs()
    conn = pymysql.connect(**kw, cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4")
    cur = conn.cursor()

    cabang_filter = ""
    if args.cabang_ids:
        ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]
        cabang_filter = "AND id IN (%s)" % ",".join(str(x) for x in ids)

    cur.execute(f"SELECT id, nama, aktif FROM cabang WHERE aktif=1 {cabang_filter} ORDER BY id")
    cabangs = cur.fetchall()

    def one(sql):
        cur.execute(sql)
        row = cur.fetchone()
        return row or {}

    print("=" * 130)
    print(f"{'Cbg':>3} {'fkt_tt':>6} {'net_tt':>14} {'byr_tt':>14} {'sis_tt':>14} | "
          f"{'lun_tt':>5} {'byrLH_tt':>14} {'det_tt':>6} {'sumDet_tt':>14} | "
          f"{'slsh_agr':>14} {'slsh_lh':>14} | "
          f"{'fkt_btl':>6} {'net_btl':>14} {'lun_btl':>5} {'byr_btl':>14}")
    print("=" * 130)
    totals = {"faktur_tt": 0, "net_tt": 0, "bayar_tt": 0, "sisa_tt": 0,
              "lunas_tt": 0, "bayar_lh_tt": 0, "detail_tt": 0, "sum_det_tt": 0,
              "faktur_btl": 0, "net_btl": 0, "lunas_btl": 0, "bayar_btl": 0}
    for c in cabangs:
        cid = c["id"]
        p = one(f"SELECT COUNT(*) n, COALESCE(SUM(total_net_rp),0) v, "
                f"COALESCE(SUM(total_bayar_rp),0) b, COALESCE(SUM(total_sisa_rp),0) s "
                f"FROM brighter_persediaan_pembelian "
                f"WHERE status_dok='Tertutup' AND cabang_id={cid}")
        h = one(f"SELECT COUNT(*) n, COALESCE(SUM(bayar),0) v "
                f"FROM brighter_transaksi_pelunasan_hutang "
                f"WHERE stat_dok='Tertutup' AND cabang_id={cid}")
        d = one(f"SELECT COUNT(*) n, COALESCE(SUM(d.nilai_bayar),0) v "
                f"FROM brighter_transaksi_pelunasan_hutang_detail d "
                f"JOIN brighter_transaksi_pelunasan_hutang h ON h.id=d.master_lunas_id "
                f"WHERE h.stat_dok='Tertutup' AND h.cabang_id={cid} "
                f"AND d.master_lunas_id IS NOT NULL")
        pb = one(f"SELECT COUNT(*) n, COALESCE(SUM(COALESCE(total_net_rp,0)),0) v "
                 f"FROM brighter_persediaan_pembelian "
                 f"WHERE status_dok='Batal' AND cabang_id={cid}")
        hb = one(f"SELECT COUNT(*) n, COALESCE(SUM(bayar),0) v "
                 f"FROM brighter_transaksi_pelunasan_hutang "
                 f"WHERE stat_dok='Batal' AND cabang_id={cid}")
        faktur_tt, net_tt, bayar_tt, sisa_tt = p["n"], _dec(p["v"]), _dec(p["b"]), _dec(p["s"])
        lunas_tt, bayar_lh_tt = h["n"], _dec(h["v"])
        detail_tt, sum_det_tt = d["n"], _dec(d["v"])
        faktur_btl, net_btl = pb["n"], _dec(pb["v"])
        lunas_btl, bayar_btl = hb["n"], _dec(hb["v"])
        slsh_agr = bayar_lh_tt - bayar_tt
        slsh_lh = bayar_lh_tt - sum_det_tt
        print(f"{cid:>3} {faktur_tt:>6} {net_tt:>14,.0f} {bayar_tt:>14,.0f} {sisa_tt:>14,.0f} | "
              f"{lunas_tt:>5} {bayar_lh_tt:>14,.0f} {detail_tt:>6} {sum_det_tt:>14,.0f} | "
              f"{slsh_agr:>14,.0f} {slsh_lh:>14,.0f} | "
              f"{faktur_btl:>6} {net_btl:>14,.0f} {lunas_btl:>5} {bayar_btl:>14,.0f}")
        for k in totals:
            totals[k] += {"faktur_tt": faktur_tt, "net_tt": net_tt, "bayar_tt": bayar_tt,
                          "sisa_tt": sisa_tt, "lunas_tt": lunas_tt, "bayar_lh_tt": bayar_lh_tt,
                          "detail_tt": detail_tt, "sum_det_tt": sum_det_tt,
                          "faktur_btl": faktur_btl, "net_btl": net_btl,
                          "lunas_btl": lunas_btl, "bayar_btl": bayar_btl}[k]
    print("=" * 130)
    t = totals
    print(f"{'TOT':>3} {t['faktur_tt']:>6} {t['net_tt']:>14,.0f} {t['bayar_tt']:>14,.0f} {t['sisa_tt']:>14,.0f} | "
          f"{t['lunas_tt']:>5} {t['bayar_lh_tt']:>14,.0f} {t['detail_tt']:>6} {t['sum_det_tt']:>14,.0f} | "
          f"{t['bayar_lh_tt']-t['bayar_tt']:>14,.0f} {t['bayar_lh_tt']-t['sum_det_tt']:>14,.0f} | "
          f"{t['faktur_btl']:>6} {t['net_btl']:>14,.0f} {t['lunas_btl']:>5} {t['bayar_btl']:>14,.0f}")
    print()

    print("-- Cek 1: faktur Tertutup inkonsistensi internal (sisa != net - bayar) --")
    bad = one(f"SELECT COUNT(*) n FROM brighter_persediaan_pembelian "
              f"WHERE status_dok='Tertutup' "
              f"AND ABS(COALESCE(total_sisa_rp,0) "
              f"- (COALESCE(total_net_rp,0) - COALESCE(total_bayar_rp,0))) > {TOLERANCE}")
    print(f"  -> {bad['n']} faktur tidak konsisten")
    if bad["n"]:
        cur.execute(f"SELECT id, cabang_id, nobukti, total_net_rp, total_bayar_rp, total_sisa_rp "
                    f"FROM brighter_persediaan_pembelian "
                    f"WHERE status_dok='Tertutup' "
                    f"AND ABS(COALESCE(total_sisa_rp,0) "
                    f"- (COALESCE(total_net_rp,0) - COALESCE(total_bayar_rp,0))) > {TOLERANCE} LIMIT 10")
        for r in cur.fetchall():
            print(f"     {r['id']} cbg {r['cabang_id']}: {r['nobukti']} "
                  f"net={r['total_net_rp']} bayar={r['total_bayar_rp']} sisa={r['total_sisa_rp']}")

    print("-- Cek 2: faktur Tertutup direferensikan >1 kali oleh detail pelunasan --")
    dup = one(f"SELECT COUNT(*) n FROM ("
              f"SELECT d.master_hutang_data_pembelian_id "
              f"FROM brighter_transaksi_pelunasan_hutang_detail d "
              f"JOIN brighter_transaksi_pelunasan_hutang h ON h.id=d.master_lunas_id "
              f"JOIN brighter_persediaan_pembelian p ON p.id=d.master_hutang_data_pembelian_id "
              f"WHERE h.stat_dok='Tertutup' AND p.status_dok='Tertutup' "
              f"AND d.master_hutang_data_pembelian_id IS NOT NULL "
              f"GROUP BY d.master_hutang_data_pembelian_id HAVING COUNT(*)>1) t")
    print(f"  -> {dup['n']} faktur dirujuk berulang")
    if dup["n"]:
        cur.execute(f"SELECT d.master_hutang_data_pembelian_id, COUNT(*) n, "
                    f"ROUND(SUM(d.nilai_bayar)) sum_nilai "
                    f"FROM brighter_transaksi_pelunasan_hutang_detail d "
                    f"JOIN brighter_transaksi_pelunasan_hutang h ON h.id=d.master_lunas_id "
                    f"JOIN brighter_persediaan_pembelian p ON p.id=d.master_hutang_data_pembelian_id "
                    f"WHERE h.stat_dok='Tertutup' AND p.status_dok='Tertutup' "
                    f"AND d.master_hutang_data_pembelian_id IS NOT NULL "
                    f"GROUP BY d.master_hutang_data_pembelian_id "
                    f"HAVING COUNT(*)>1 ORDER BY n DESC LIMIT 10")
        for r in cur.fetchall():
            print(f"     faktur {r['master_hutang_data_pembelian_id']}: "
                  f"{r['n']}x referensi, total detail {r['sum_nilai']}")

    print("-- Cek 3: header pelunasan Tertutup bayar vs SUM(detail) mismatch --")
    mm = one(f"SELECT COUNT(*) n FROM ("
             f"SELECT h.id, h.bayar, COALESCE(SUM(d.nilai_bayar),0) sum_det "
             f"FROM brighter_transaksi_pelunasan_hutang h "
             f"LEFT JOIN brighter_transaksi_pelunasan_hutang_detail d ON d.master_lunas_id=h.id "
             f"WHERE h.stat_dok='Tertutup' "
             f"GROUP BY h.id, h.bayar "
             f"HAVING ABS(COALESCE(SUM(d.nilai_bayar),0) - h.bayar) > {TOLERANCE}) t")
    print(f"  -> {mm['n']} header mismatch")
    if mm["n"]:
        cur.execute(f"SELECT h.id, MIN(h.nobukti) nobukti, MIN(h.cabang_id) cabang_id, h.bayar, "
                    f"COALESCE(SUM(d.nilai_bayar),0) sum_det "
                    f"FROM brighter_transaksi_pelunasan_hutang h "
                    f"LEFT JOIN brighter_transaksi_pelunasan_hutang_detail d ON d.master_lunas_id=h.id "
                    f"WHERE h.stat_dok='Tertutup' "
                    f"GROUP BY h.id, h.bayar "
                    f"HAVING ABS(COALESCE(SUM(d.nilai_bayar),0) - h.bayar) > {TOLERANCE} LIMIT 10")
        for r in cur.fetchall():
            print(f"     {r['nobukti']} cbg {r['cabang_id']}: bayar={r['bayar']} "
                  f"sum_detail={r['sum_det']}")

    print("-- Cek 4: faktur Tertutup tanpa detail pelunasan --")
    nodet = one(f"SELECT COUNT(*) n, COALESCE(SUM(total_sisa_rp),0) s "
                f"FROM brighter_persediaan_pembelian p "
                f"WHERE p.status_dok='Tertutup' "
                f"AND NOT EXISTS (SELECT 1 FROM brighter_transaksi_pelunasan_hutang_detail d "
                f"WHERE d.master_hutang_data_pembelian_id=p.id)")
    print(f"  -> {nodet['n']} faktur tanpa detail (sisa total {nodet['s']:,.0f})")
    ltn = one(f"SELECT COUNT(*) n FROM brighter_persediaan_pembelian p "
              f"WHERE p.status_dok='Tertutup' AND p.status_lunas='Lunas' "
              f"AND COALESCE(p.total_sisa_rp,0)=0 "
              f"AND NOT EXISTS (SELECT 1 FROM brighter_transaksi_pelunasan_hutang_detail d "
              f"WHERE d.master_hutang_data_pembelian_id=p.id)")
    print(f"  -> {ltn['n']} faktur Lunas tapi tanpa detail (perlu dicek)")
    if ltn["n"]:
        cur.execute(f"SELECT p.id, p.nobukti, p.cabang_id, "
                    f"p.supplier_data_supplier_nama, "
                    f"p.total_net_rp FROM brighter_persediaan_pembelian p "
                    f"WHERE p.status_dok='Tertutup' AND p.status_lunas='Lunas' "
                    f"AND COALESCE(p.total_sisa_rp,0)=0 "
                    f"AND NOT EXISTS (SELECT 1 FROM brighter_transaksi_pelunasan_hutang_detail d "
                    f"WHERE d.master_hutang_data_pembelian_id=p.id) LIMIT 10")
        for r in cur.fetchall():
            print(f"     {r['nobukti']} cbg {r['cabang_id']}: net={r['total_net_rp']} "
                  f"({r['supplier_data_supplier_nama']})")

    print("-- Cek 5: faktur Tertutup overpaid (total_sisa_rp < 0) --")
    ov = one(f"SELECT COUNT(*) n, COALESCE(SUM(total_sisa_rp),0) s "
             f"FROM brighter_persediaan_pembelian "
             f"WHERE status_dok='Tertutup' AND total_sisa_rp < -{TOLERANCE}")
    print(f"  -> {ov['n']} faktur overpaid (jumlah sisa negatif {ov['s']:,.0f})")
    if ov["n"]:
        cur.execute(f"SELECT id, cabang_id, nobukti, total_net_rp, total_bayar_rp, total_sisa_rp "
                    f"FROM brighter_persediaan_pembelian "
                    f"WHERE status_dok='Tertutup' AND total_sisa_rp < -{TOLERANCE} LIMIT 10")
        for r in cur.fetchall():
            print(f"     {r['id']} cbg {r['cabang_id']}: {r['nobukti']} "
                  f"net={r['total_net_rp']} bayar={r['total_bayar_rp']} sisa={r['total_sisa_rp']}")

    print("-- Cek 6: detail pelunasan record sampah (NULL / id hash) --")
    junk = one(f"SELECT COUNT(*) n FROM brighter_transaksi_pelunasan_hutang_detail "
               f"WHERE master_lunas_id IS NULL OR nilai_bayar IS NULL")
    print(f"  -> {junk['n']} record sampah (berasal dari detail API `[{{}}]` / 404)")

    print("-- Info Batal (historical, TIDAK masuk perhitungan rekon) --")
    fbtl = one(f"SELECT COUNT(*) n, COALESCE(SUM(COALESCE(total_sisa_rp,0)),0) s "
               f"FROM brighter_persediaan_pembelian WHERE status_dok='Batal'")
    hbtl = one(f"SELECT COUNT(*) n FROM brighter_transaksi_pelunasan_hutang h "
               f"WHERE h.stat_dok='Batal' "
               f"AND NOT EXISTS (SELECT 1 FROM brighter_transaksi_pelunasan_hutang_detail d "
               f"WHERE d.master_lunas_id=h.id)")
    print(f"  -> {fbtl['n']} faktur Batal (sisa {fbtl['s']:,.0f}), "
          f"{hbtl['n']} header pelunasan Batal tanpa detail")

    conn.close()
    print("\nKeterangan kolom (*_tt = rekon, Tertutup saja): faktur/net/bayar/sisa pembelian, "
          "lunas/bayar_lh header pelunasan, detail/sum_det detail pelunasan (join header, "
          "non-sampah). slsh_agr = bayar_lh - bayar faktur, slsh_lh = bayar_lh - sum_det. "
          "Kolom *_btl = info Batal (historical, tidak dihitung di rekon).")


if __name__ == "__main__":
    main()
