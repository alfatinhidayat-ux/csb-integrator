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


HEADER_MAP = {
    "id": "id",
    "cabang_id": "cabang_id",
    "tanggal": "tanggal",
    "no_bukti": "no_nota",
    "customer_id": "pelanggan",
    "keterangan": "keterangan",
    "status_dokumen": "status_dokumen",
    "bayar": "bayar",
    "cara": "kombinasi_kanal",
    "card_jenis": "kartu_jenis",
    "card_edc": "kartu_edc",
    "card_no": "kartu_no",
    "total_biaya": "total_biaya",
    "cbayar_nama": "kombinasi_kanal",
    "cbayar_nilai_bayar_rp": "total_bayar",
    "cbayar_card_jenis": "kartu_jenis",
    "cbayar_card_edc": "kartu_edc",
    "cbayar_card_no": "kartu_no",
    "cbayar_transfer_nama": "nama_transfer",
}

DETAIL_MAP = {
    "id": "id",
    "cabang_id": "cabang_id",
    "pos_id": "pos_id",
    "produk_id": "produk_id",
    "jumlah": "jumlah",
    "jumlah_retur": "jml_retur",
    "harga": "harga_satuan",
    "diskon": "diskon_persen",
    "diskon_rp": "diskon_rp",
    "produk_kode": "kode_produk",
    "produk_nama": "nama_produk",
    "produk_sku": "sku",
    "produk_group": "grup",
    "produk_group_sub": "sub_grup",
    "produk_brand": "merek",
    "satuan_code": "kode_satuan",
    "satuan_nama": "satuan",
}


def build_sql(dst, src, mapping, extra_cols=()):
    """Build INSERT ... SELECT ... WHERE NOT EXISTS for one table."""
    dst_cols = list(mapping.keys()) + list(extra_cols)
    src_cols = list(mapping.values())
    dst_names = ", ".join(f"`{c}`" for c in dst_cols)
    src_sel = ", ".join(f"`{c}`" for c in src_cols)
    extra_sel = ", ".join(["%s"] * len(extra_cols)) if extra_cols else ""
    if extra_cols:
        src_sel = f"{src_sel}, {extra_sel}"
    match = " AND ".join(f"d.`{c}` = s.`{c}`" for c in ("id", "cabang_id"))
    return f"""
        INSERT INTO `{dst}` ({dst_names})
        SELECT {src_sel} FROM `{src}` s
        WHERE NOT EXISTS (
            SELECT 1 FROM `{dst}` d WHERE {match}
        )
    """


def main():
    parser = argparse.ArgumentParser(
        description="Backfill missing POS data from pos_ok/pos_ok_detail into brighter_pos/brighter_pos_detail"
    )
    parser.add_argument(
        "-e", "--env", action="store_true",
        help="Load configuration from environment variables",
    )
    parser.add_argument(
        "--preview", action="store_true",
        help="Only report how many rows would be backfilled (no writes)",
    )
    parser.add_argument(
        "--only-header", action="store_true",
        help="Only backfill headers (skip details)",
    )
    parser.add_argument(
        "--only-detail", action="store_true",
        help="Only backfill details (skip headers)",
    )
    args = parser.parse_args()

    cfg = Config.from_env()
    conn = connect(cfg)
    try:
        if not args.only_detail:
            header_sql = build_sql("brighter_pos", "pos_ok", HEADER_MAP)
            cur = conn.cursor()
            if args.preview:
                match = " AND ".join(f"d.id = s.id" for _ in ("id",)) + " AND d.cabang_id = s.cabang_id"
                cur.execute(
                    "SELECT COUNT(*) AS n FROM pos_ok s WHERE NOT EXISTS "
                    f"(SELECT 1 FROM brighter_pos d WHERE {match})"
                )
                print("Headers to backfill:", cur.fetchone()["n"])
            else:
                cur.execute(header_sql)
                print("Headers backfilled:", cur.rowcount)
            conn.commit()

        if not args.only_header:
            detail_sql = build_sql("brighter_pos_detail", "pos_ok_detail", DETAIL_MAP)
            cur = conn.cursor()
            if args.preview:
                match = "d.id = s.id AND d.cabang_id = s.cabang_id"
                cur.execute(
                    "SELECT COUNT(*) AS n FROM pos_ok_detail s WHERE NOT EXISTS "
                    f"(SELECT 1 FROM brighter_pos_detail d WHERE {match})"
                )
                print("Details to backfill:", cur.fetchone()["n"])
            else:
                cur.execute(detail_sql)
                print("Details backfilled:", cur.rowcount)
            conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
