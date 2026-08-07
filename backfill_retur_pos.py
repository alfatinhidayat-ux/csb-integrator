import argparse
import os
import sys

import pymysql

sys.path.insert(0, os.getcwd())

from config import Config


def connect(cfg):
    return pymysql.connect(
        host=cfg.csb_db_host or cfg.db_host,
        port=cfg.csb_db_port or cfg.db_port,
        user=cfg.csb_db_user or cfg.db_user,
        password=cfg.csb_db_password if cfg.csb_db_password is not None else cfg.db_password,
        database=cfg.csb_db_name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Backfill pos_transaction_id / pos_transaction_item_id on "
                    "retur_penjualan_detail from brighter_retur_penjualan -> pos_transactions"
    )
    parser.add_argument("-e", "--env", action="store_true", help="Load config from env")
    parser.add_argument("--preview", action="store_true", help="Only report numbers, no writes")
    parser.add_argument("--item", action="store_true",
                        help="Also fill pos_transaction_item_id (match by product within the POS batch)")
    args = parser.parse_args()

    cfg = Config.from_env()
    conn = connect(cfg)
    try:
        cur = conn.cursor()

        # 1) pos_transaction_id:
        #    retur_penjualan.id -> client_request_id 'brighter-retur-<X>' -> brighter_retur.id
        #    -> jproduk_id -> pos_transactions.legacy_id
        if args.preview:
            cur.execute("""
                SELECT COUNT(*) n FROM retur_penjualan_detail rd
                JOIN retur_penjualan rp ON rp.id = rd.retur_penjualan_id
                JOIN brighter_retur_penjualan br
                  ON CAST(REPLACE(rp.client_request_id,'brighter-retur-','') AS UNSIGNED) = br.id
                 AND br.cabang_id = rp.cabang_id
                JOIN pos_transactions pt ON pt.legacy_id = br.jproduk_id AND pt.cabang_id = br.cabang_id
            """)
            print("retur_penjualan_detail dapat diisi pos_transaction_id:", cur.fetchone()["n"])
        else:
            cur.execute("""
                UPDATE retur_penjualan_detail rd
                JOIN retur_penjualan rp ON rp.id = rd.retur_penjualan_id
                JOIN brighter_retur_penjualan br
                  ON CAST(REPLACE(rp.client_request_id,'brighter-retur-','') AS UNSIGNED) = br.id
                 AND br.cabang_id = rp.cabang_id
                JOIN pos_transactions pt ON pt.legacy_id = br.jproduk_id AND pt.cabang_id = br.cabang_id
                SET rd.pos_transaction_id = pt.id
                WHERE rd.pos_transaction_id IS NULL OR rd.pos_transaction_id = 0
            """)
            print("pos_transaction_id diupdate:", cur.rowcount)
            conn.commit()

        # 2) pos_transaction_item_id: match dalam batch POS yang sama memakai product_id
        if args.item:
            if args.preview:
                cur.execute("""
                    SELECT COUNT(*) n FROM retur_penjualan_detail rd
                    JOIN pos_transaction_items it
                      ON it.pos_transaction_id = rd.pos_transaction_id
                     AND it.product_id = rd.produk_id
                    WHERE rd.pos_transaction_id IS NOT NULL AND rd.pos_transaction_id <> 0
                """)
                print("detail dengan pos_transaction_item_id match (by product):", cur.fetchone()["n"])
            else:
                cur.execute("""
                    UPDATE retur_penjualan_detail rd
                    JOIN pos_transaction_items i
                      ON i.pos_transaction_id = rd.pos_transaction_id
                     AND i.product_id = rd.produk_id
                    SET rd.pos_transaction_item_id = i.id
                    WHERE rd.pos_transaction_id IS NOT NULL AND rd.pos_transaction_id <> 0
                """)
                print("pos_transaction_item_id diupdate:", cur.rowcount)
            conn.commit()
        else:
            conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()