import argparse
import os
import sys

import pymysql

sys.path.insert(0, os.getcwd())
from config import Config


def main():
    parser = argparse.ArgumentParser(description="Rekonsiliasi nominal pembelian (Gross, Diskon, Net)")
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
        cabang_filter = "AND cabang_id IN (%s)" % ",".join(str(x) for x in ids)

    # Fetch summary grouped by cabang and status_dok
    sql = f"""
        SELECT 
            cabang_id,
            status_dok,
            COUNT(*) as count,
            COALESCE(SUM(total_biaya_rp), 0) as sum_bruto,
            COALESCE(SUM(total_diskon_rp), 0) as sum_diskon,
            COALESCE(SUM(total_net_rp), 0) as sum_net
        FROM brighter_persediaan_pembelian
        WHERE 1=1 {cabang_filter}
        GROUP BY cabang_id, status_dok
        ORDER BY cabang_id, status_dok
    """
    
    cur.execute(sql)
    rows = cur.fetchall()

    print("=" * 105)
    print(f"{'Cbg':>3} {'Status':<12} {'Count':>6} {'Total Gross':>18} {'Total Diskon Rp':>18} {'Disk %':>8} {'Total Net':>18}")
    print("=" * 105)

    totals = {
        "count": 0,
        "bruto": 0.0,
        "diskon": 0.0,
        "net": 0.0
    }

    for r in rows:
        bruto = float(r["sum_bruto"])
        diskon = float(r["sum_diskon"])
        net = float(r["sum_net"])
        
        pct = (diskon / bruto * 100) if bruto > 0 else 0.0
        
        print(f"{r['cabang_id']:>3} {r['status_dok']:<12} {r['count']:>6} {bruto:>18,.2f} {diskon:>18,.2f} {pct:>7.2f}% {net:>18,.2f}")
        
        totals["count"] += r["count"]
        totals["bruto"] += bruto
        totals["diskon"] += diskon
        totals["net"] += net

    print("=" * 105)
    tot_pct = (totals["diskon"] / totals["bruto"] * 100) if totals["bruto"] > 0 else 0.0
    print(f"{'TOT':>3} {'':<12} {totals['count']:>6} {totals['bruto']:>18,.2f} {totals['diskon']:>18,.2f} {tot_pct:>7.2f}% {totals['net']:>18,.2f}")
    print("=" * 105)

    conn.close()


if __name__ == "__main__":
    main()
