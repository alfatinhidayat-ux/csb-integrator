"""Mirror header pelunasan piutang Brighter ke piutang_pelunasan.

Dipakai setelah staging `brighter_transaksi_pelunasan_piutang` sudah lengkap.
Script ini tidak posting KasBank dan tidak menghapus data; ia memastikan tiap
nomor LP Brighter punya header Clarify, lalu menonaktifkan header fallback legacy
yang tidak ada di Brighter untuk periode yang sama agar cash-in tidak dobel.
"""

from __future__ import annotations

import argparse
from datetime import datetime

import pymysql

from config import Config


CARA_MAP = {
    "tunai": "tunai",
    "transfer": "transfer",
    "debit": "debit",
    "qris": "qris",
    "qris_barcode": "qris",
    "card": "debit",
    "edc": "debit",
    "deposit": "deposit",
}


def parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def map_method(value: str | None) -> str:
    return CARA_MAP.get((value or "").strip().lower(), "tunai")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mirror header LP Brighter -> piutang_pelunasan")
    parser.add_argument("--tanggal-awal", default="2026-01-01")
    parser.add_argument("--tanggal-akhir", default="2026-01-31")
    parser.add_argument("--cabang-ids", default="1,2,4,5")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    start = parse_date(args.tanggal_awal)
    end = parse_date(args.tanggal_akhir)
    cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]

    cfg = Config.from_env()
    conn = pymysql.connect(**cfg.csb_db_kwargs(), cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4", autocommit=False)
    created = 0
    updated = 0
    superseded = 0
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                b.cabang_id, b.nobukti, MIN(b.tanggal) AS tanggal,
                MIN(b.cust) AS cust, MIN(b.cara) AS cara, SUM(COALESCE(b.bayar, 0)) AS bayar,
                NULLIF(TRIM(GROUP_CONCAT(DISTINCT NULLIF(TRIM(COALESCE(b.keterangan, '')), '') SEPARATOR ' | ')), '') AS keterangan,
                MIN(b.timestamp_data_created_by) AS timestamp_data_created_by,
                MIN(b.timestamp_data_created_at) AS timestamp_data_created_at,
                MIN(c.id) AS customer_id,
                SUBSTRING_INDEX(
                    GROUP_CONCAT(DISTINCT COALESCE(c.kode, b.cust_data_cust_no) COLLATE utf8mb4_unicode_ci ORDER BY COALESCE(c.kode, b.cust_data_cust_no) SEPARATOR ','),
                    ',',
                    1
                ) AS customer_kode,
                SUBSTRING_INDEX(
                    GROUP_CONCAT(DISTINCT COALESCE(c.nama, b.cust_data_cust_nama) COLLATE utf8mb4_unicode_ci ORDER BY COALESCE(c.nama, b.cust_data_cust_nama) SEPARATOR ','),
                    ',',
                    1
                ) AS customer_nama
            FROM brighter_transaksi_pelunasan_piutang b
            LEFT JOIN customer c ON c.id = b.cust
            WHERE b.stat_dok = 'Tertutup'
              AND b.tanggal BETWEEN %s AND %s
              AND b.cabang_id IN ({})
              AND b.nobukti IS NOT NULL
            GROUP BY b.cabang_id, b.nobukti
            ORDER BY b.cabang_id, MIN(b.tanggal), b.nobukti
            """.format(",".join(["%s"] * len(cabang_ids))),
            [start.isoformat(), end.isoformat(), *cabang_ids],
        )
        rows = cur.fetchall()

        print("=" * 100)
        print(f"MIRROR LP BRIGHTER {start}..{end} cabang={cabang_ids} mode={'APPLY' if args.apply else 'DRY-RUN'}")
        print(f"Header Brighter: {len(rows)}")
        print("=" * 100)

        for row in rows:
            cur.execute(
                """
                SELECT id FROM piutang_pelunasan
                WHERE cabang_id = %s
                  AND pelunasan_number COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci
                LIMIT 1
                """,
                (row["cabang_id"], row["nobukti"]),
            )
            existing = cur.fetchone()
            payload = {
                "pelunasan_number": row["nobukti"],
                "cabang_id": row["cabang_id"],
                "customer_id": row["customer_id"],
                "customer_kode": row["customer_kode"],
                "customer_nama": row["customer_nama"],
                "tanggal": row["tanggal"],
                "payment_method": map_method(row["cara"]),
                "total_piutang": row["bayar"],
                "total_terbayar_sebelumnya": 0,
                "total_pelunasan": row["bayar"],
                "total_sisa": 0,
                "keterangan": row["keterangan"] or f"Mirror Brighter LP {row['nobukti']}",
                "status": "posted",
                "dibuat_oleh": 0,
                "dibuat_oleh_username": row["timestamp_data_created_by"] or "brighter",
                "is_legacy": 1,
            }

            if existing:
                updated += 1
                if args.apply:
                    cur.execute(
                        """
                        UPDATE piutang_pelunasan
                        SET customer_id=%(customer_id)s, customer_kode=%(customer_kode)s,
                            customer_nama=%(customer_nama)s, tanggal=%(tanggal)s,
                            payment_method=%(payment_method)s, total_piutang=%(total_piutang)s,
                            total_terbayar_sebelumnya=%(total_terbayar_sebelumnya)s,
                            total_pelunasan=%(total_pelunasan)s, total_sisa=%(total_sisa)s,
                            keterangan=%(keterangan)s, status='posted', is_legacy=1,
                            updated_at=NOW()
                        WHERE id=%(id)s
                        """,
                        {**payload, "id": existing["id"]},
                    )
            else:
                created += 1
                if args.apply:
                    cur.execute(
                        """
                        INSERT INTO piutang_pelunasan
                            (pelunasan_number, cabang_id, customer_id, customer_kode, customer_nama,
                             tanggal, payment_method, payment_detail, total_piutang,
                             total_terbayar_sebelumnya, total_pelunasan, total_sisa,
                             keterangan, status, dibuat_oleh, dibuat_oleh_username,
                             is_legacy, created_at, updated_at)
                        VALUES
                            (%(pelunasan_number)s, %(cabang_id)s, %(customer_id)s, %(customer_kode)s, %(customer_nama)s,
                             %(tanggal)s, %(payment_method)s, NULL, %(total_piutang)s,
                             %(total_terbayar_sebelumnya)s, %(total_pelunasan)s, %(total_sisa)s,
                             %(keterangan)s, %(status)s, %(dibuat_oleh)s, %(dibuat_oleh_username)s,
                             %(is_legacy)s, NOW(), NOW())
                        """,
                        payload,
                    )

        # Header fallback SYS yang dibuat dari grouping lama tidak boleh ikut cash-in
        # bila tidak ada nomor LP tersebut di Brighter pada periode yang sama.
        cur.execute(
            """
            SELECT p.id, p.pelunasan_number, p.total_pelunasan
            FROM piutang_pelunasan p
            LEFT JOIN brighter_transaksi_pelunasan_piutang b
              ON b.cabang_id = p.cabang_id
             AND b.nobukti COLLATE utf8mb4_unicode_ci = p.pelunasan_number COLLATE utf8mb4_unicode_ci
            WHERE p.is_legacy = 1
              AND p.status = 'posted'
              AND p.tanggal BETWEEN %s AND %s
              AND p.cabang_id IN ({})
              AND b.id IS NULL
              AND (p.pelunasan_number LIKE %s OR p.pelunasan_number LIKE %s OR p.pelunasan_number LIKE %s)
            """.format(",".join(["%s"] * len(cabang_ids))),
            [start.isoformat(), end.isoformat(), *cabang_ids, "%/SYS/LP/%", "SYS/%/LP/%", "%/SYS/%"],
        )
        stale = cur.fetchall()
        superseded = len(stale)
        if args.apply and stale:
            cur.execute(
                "UPDATE piutang_pelunasan SET status='superseded', updated_at=NOW() WHERE id IN ({})".format(
                    ",".join(["%s"] * len(stale))
                ),
                [r["id"] for r in stale],
            )

        if args.apply:
            conn.commit()
        else:
            conn.rollback()

        print(f"Header baru      : {created}")
        print(f"Header update    : {updated}")
        print(f"Fallback nonaktif: {superseded}")
        print("OK" if args.apply else "DRY-RUN selesai, tidak ada perubahan.")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
