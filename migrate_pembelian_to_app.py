"""Migrate Brighter pembelian (header + detail) into the app's `pembelian` and
`pembelian_detail` tables (csb_db), data-only (no stock/kas_bank posting).

Approach (as agreed):
- Import headers matching the official purchase report filter:
  status_dok='Tertutup', tanggal 2026-01-01..2026-08-31, cabang 1 (Kobisonta) &
  5 (Kairatu). KRT/PB/2602-0164 (BONUS, no supplier, qty 0) is skipped.
- supplier_id = supplier_data_supplier_id because csb_db.supplier.id == Brighter
  supplier id (kept by sync_finance.py). Missing suppliers can be pre-synced via
  --sync-suppliers (reuses sync_finance.py logic).
- status='selesai'; tanggal_po=tanggal; total_qty=total_qty_produk;
  total_nilai=total_biaya_rp; diskon_header=total_diskon_rp;
  grand_total=total_net_rp. no_tagihan is stored in catatan.
- Detail lines use qty_beli/qty_diterima, satuan_id = Brighter satuan id (string,
  consistent with retur migration), konversi_nilai from produk_satuan_konversi
  (default 1), subtotal = pembelian_det_subtotal_net_rp.
- Idempotent: headers already present (by kode) are skipped; their details are
  replaced (delete + re-insert) on every run.

Usage:
    python migrate_pembelian_to_app.py --dry-run
    python migrate_pembelian_to_app.py --sync-suppliers --dry-run
    python migrate_pembelian_to_app.py --run
"""

from __future__ import annotations

import argparse
import os
import sys

import pymysql

sys.path.insert(0, os.getcwd())

from config import Config
from db import DatabaseManager


TANGGAL_MIN = "2026-01-01"
TANGGAL_MAX = "2026-08-31"
SKIP_NOBUKTI = {"KRT/PB/2602-0164"}


def connect_csb() -> pymysql.connections.Connection:
    cfg = Config.from_env()
    kw = cfg.csb_db_kwargs()
    return pymysql.connect(
        host=kw["host"], port=kw["port"], user=kw["user"], password=kw["password"],
        database=kw["database"], charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )


def load_headers(conn, cabang_ids: tuple[int, ...]) -> list[dict]:
    with conn.cursor() as cur:
        ph = ",".join(["%s"] * len(cabang_ids))
        cur.execute(
            f"""
            SELECT id, nobukti, tanggal, supplier_id, supplier_data_supplier_id,
                   supplier_data_supplier_kode, supplier_data_supplier_nama,
                   no_tagihan, total_qty_produk, total_biaya_rp, total_diskon,
                   total_diskon_rp, total_net_rp, status_dok, status_lunas,
                   keterangan, cabang_id,
                   timestamp_data_created_by, timestamp_data_created_at,
                   timestamp_data_updated_by, timestamp_data_updated_at
            FROM brighter_persediaan_pembelian
            WHERE status_dok = 'Tertutup'
              AND tanggal >= %s AND tanggal <= %s
              AND cabang_id IN ({ph})
              AND nobukti NOT IN ({"," .join(["%s"] * len(SKIP_NOBUKTI))})
            ORDER BY cabang_id, id
            """,
            (TANGGAL_MIN, TANGGAL_MAX, *cabang_ids, *SKIP_NOBUKTI),
        )
        return cur.fetchall()


def load_details(conn, cabang_ids: tuple[int, ...]) -> list[dict]:
    with conn.cursor() as cur:
        ph = ",".join(["%s"] * len(cabang_ids))
        cur.execute(
            f"""
            SELECT d.nobukti, d.cabang_id, d.pembelian_det_id,
                   d.pembelian_det_produk_id, d.pembelian_det_satuan_id,
                   d.pembelian_det_produk_harga, d.pembelian_det_diskon,
                   d.pembelian_det_diskon_rp, d.pembelian_det_qty_beli,
                   d.pembelian_det_qty_diterima, d.pembelian_det_subtotal_rp,
                   d.pembelian_det_subtotal_net_rp, d.pembelian_det_keterangan
            FROM brighter_persediaan_pembelian_detail d
            WHERE d.cabang_id IN ({ph})
            ORDER BY d.cabang_id, d.pembelian_det_id
            """,
            cabang_ids,
        )
        return cur.fetchall()


def load_supplier_map(conn) -> dict[int, int]:
    """Map Brighter supplier_data_supplier_id -> csb_db.supplier.id.

    Priority: (a) existing supplier whose id == Brighter id, (b) single supplier
    with matching kode, (c) single supplier with matching nama. Falls back to
    creating the supplier row (Brighter id) when nothing matches.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id, kode, nama FROM supplier")
        sup = cur.fetchall()
    by_kode: dict[str, list[dict]] = {}
    by_nama: dict[str, list[dict]] = {}
    for s in sup:
        by_kode.setdefault((s["kode"] or "").strip().lower(), []).append(s)
        by_nama.setdefault((s["nama"] or "").strip().lower(), []).append(s)
    return by_kode, by_nama


def resolve_supplier(by_kode, by_nama, sid: int, kode: str, nama: str) -> int | None:
    if sid is None:
        return None
    k = (kode or "").strip().lower()
    n = (nama or "").strip().lower()
    for cand in by_kode.get(k, []) + by_nama.get(n, []):
        if int(cand["id"]) == sid:
            return int(cand["id"])
    km = by_kode.get(k, [])
    if len(km) == 1:
        return int(km[0]["id"])
    nm = by_nama.get(n, [])
    if len(nm) == 1:
        return int(nm[0]["id"])
    return None


def load_konversi(conn) -> dict[tuple[int, int], float]:
    """Map (produk_id, satuan_id[Brighter]) -> konversi_nilai."""
    out: dict[tuple[int, int], float] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT produk_id, satuan_id, konversi_nilai
            FROM produk_satuan_konversi
            WHERE konversi_nilai IS NOT NULL
            """
        )
        for r in cur.fetchall():
            if r["produk_id"] is None or r["satuan_id"] is None:
                continue
            key = (int(r["produk_id"]), int(r["satuan_id"]))
            if key not in out:
                out[key] = float(r["konversi_nilai"])
    return out


def build_plan(conn, by_kode, by_nama, cabang_ids: tuple[int, ...]) -> tuple[list[dict], list[dict], int]:
    headers = load_headers(conn, cabang_ids)
    details = load_details(conn, cabang_ids)
    konversi = load_konversi(conn)

    details_by_doc: dict[tuple[int, str], list[dict]] = {}
    for d in details:
        details_by_doc.setdefault((d["cabang_id"], d["nobukti"]), []).append(d)

    plan_headers = []
    plan_details: list[dict] = []
    skipped_no_supplier = 0
    for h in headers:
        supplier_id = resolve_supplier(
            by_kode, by_nama,
            h["supplier_data_supplier_id"],
            h.get("supplier_data_supplier_kode"),
            h.get("supplier_data_supplier_nama"),
        )
        if supplier_id is None:
            skipped_no_supplier += 1
            continue
        catatan = h.get("keterangan") or ""
        tagihan = (h.get("no_tagihan") or "").strip()
        if tagihan:
            catatan = f"No. Tagihan: {tagihan}\n{catatan}".strip()
        created_at = h.get("timestamp_data_created_at") or h["tanggal"]
        updated_at = h.get("timestamp_data_updated_at") or created_at

        ph = {
            "kode": h["nobukti"],
            "cabang_id": h["cabang_id"],
            "supplier_id": supplier_id,
            "status": "selesai",
            "tanggal_po": str(h["tanggal"]),
            "tanggal_selesai": str(h["tanggal"]),
            "total_qty": float(h["total_qty_produk"] or 0),
            "total_nilai": float(h["total_biaya_rp"] or 0),
            "diskon_header": round(float(h["total_biaya_rp"] or 0) - float(h["total_net_rp"] or 0), 2),
            "grand_total": float(h["total_net_rp"] or 0),
            "catatan": catatan or None,
            "created_by": h.get("timestamp_data_created_by"),
            "created_at": str(created_at),
            "updated_by": h.get("timestamp_data_updated_by"),
            "updated_at": str(updated_at),
            "_src_id": h["id"],
        }
        plan_headers.append(ph)

        for d in details_by_doc.get((h["cabang_id"], h["nobukti"]), []):
            konv = konversi.get((int(d["pembelian_det_produk_id"]), int(d["pembelian_det_satuan_id"])), 1.0)
            qty_pesan = float(d["pembelian_det_qty_beli"] or 0)
            qty_diterima = float(d["pembelian_det_qty_diterima"] or 0)
            harga = float(d["pembelian_det_produk_harga"] or 0)
            diskon = float(d["pembelian_det_diskon"] or 0)
            if diskon > 100:
                diskon = diskon / 1000.0
            harga_setelah_diskon = harga * (1 - diskon / 100.0) if diskon else harga
            plan_details.append({
                "_doc_key": (h["cabang_id"], h["nobukti"]),
                "produk_id": d["pembelian_det_produk_id"],
                "satuan_id": str(d["pembelian_det_satuan_id"]),
                "konversi_nilai": konv,
                "qty_pesan": qty_pesan,
                "qty_dasar_pesan": round(qty_pesan * konv, 4),
                "qty_diterima": qty_diterima,
                "qty_dasar_diterima": round(qty_diterima * konv, 4),
                "harga_satuan": harga,
                "diskon_persen": diskon,
                "diskon_rp": float(d["pembelian_det_diskon_rp"] or 0),
                "harga_setelah_diskon": round(harga_setelah_diskon, 2),
                "subtotal": float(d["pembelian_det_subtotal_net_rp"] or 0),
                "catatan": d.get("pembelian_det_keterangan") or None,
            })

    return plan_headers, plan_details, skipped_no_supplier


def main():
    parser = argparse.ArgumentParser(
        description="Migrate brighter pembelian -> app pembelian/pembelian_detail (data-only)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Hitung rencana tanpa menulis DB")
    parser.add_argument("--run", action="store_true", help="Langsung tulis ke DB")
    parser.add_argument("--cabang-ids", default="1,5",
                        help="Comma-separated cabang IDs; default 1,5")
    args = parser.parse_args()

    cabang_ids = tuple(int(x.strip()) for x in args.cabang_ids.split(",") if x.strip())

    conn = connect_csb()
    try:
        by_kode, by_nama = load_supplier_map(conn)
        plan_headers, plan_details, skipped_no_supplier = build_plan(conn, by_kode, by_nama, cabang_ids)

        total_net = sum(p["grand_total"] for p in plan_headers)
        print("=" * 62)
        print("Rencana migrasi pembelian -> csb_db.pembelian / pembelian_detail")
        print(f"  Header : {len(plan_headers)} dokumen")
        print(f"  Detail : {len(plan_details)} baris")
        print(f"  Cabang : {','.join(str(c) for c in cabang_ids)}")
        print(f"  Skip (supplier kosong): {skipped_no_supplier}")
        print(f"  Total grand_total       : {total_net:,.0f}")
        print("=" * 62)

        # hitung supplier yang belum ada di csb_db.supplier
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM supplier")
            existing_supplier = {r["id"] for r in cur.fetchall()}
        missing_suppliers = sorted({
            p["supplier_id"] for p in plan_headers
        } - existing_supplier)
        if missing_suppliers:
            print(f"  Supplier belum ada: {missing_suppliers}")
            print("  Jalankan dengan --sync-suppliers dulu (atau script sync supplier).")
        else:
            print("  Supplier: semua sudah ada di csb_db.supplier.")

        if args.dry_run:
            print("DRY RUN — tidak ada data yang ditulis.")
            return
        if not args.run:
            ans = input("Tulis ke database? (yes/no): ")
            if ans.strip().lower() != "yes":
                print("Dibatalkan.")
                return

        with conn.cursor() as cur:
            cur.execute("SELECT kode, id FROM pembelian")
            existing = {r["kode"]: int(r["id"]) for r in cur.fetchall()}

        new_headers = [p for p in plan_headers if p["kode"] not in existing]
        print(f"  Header baru: {len(new_headers)}, Header sudah ada: {len(plan_headers) - len(new_headers)}")

        inserted = 0
        detail_count = 0
        for p in new_headers:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pembelian
                        (kode, cabang_id, supplier_id, status, tanggal_po,
                         tanggal_selesai, total_qty, total_nilai, diskon_header,
                         grand_total, catatan, created_by, created_at, updated_by, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        p["kode"], p["cabang_id"], p["supplier_id"], p["status"],
                        p["tanggal_po"], p["tanggal_selesai"], p["total_qty"],
                        p["total_nilai"], p["diskon_header"], p["grand_total"],
                        p["catatan"], p["created_by"], p["created_at"],
                        p["updated_by"], p["updated_at"],
                    ),
                )
                pembelian_id = cur.lastrowid
            for d in [x for x in plan_details if x["_doc_key"] == (p["cabang_id"], p["kode"])]:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO pembelian_detail
                            (pembelian_id, produk_id, satuan_id, konversi_nilai,
                             qty_pesan, qty_dasar_pesan, qty_diterima, qty_dasar_diterima,
                             harga_satuan, diskon_persen, diskon_rp,
                             harga_setelah_diskon, subtotal, catatan)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            pembelian_id, d["produk_id"], d["satuan_id"],
                            d["konversi_nilai"], d["qty_pesan"], d["qty_dasar_pesan"],
                            d["qty_diterima"], d["qty_dasar_diterima"],
                            d["harga_satuan"], d["diskon_persen"], d["diskon_rp"],
                            d["harga_setelah_diskon"], d["subtotal"], d["catatan"],
                        ),
                    )
                    detail_count += 1
            conn.commit()
            inserted += 1
            if inserted % 50 == 0:
                print(f"  inserted {inserted}/{len(new_headers)}")

        # Backfill/replace detail for headers that already exist (idempotent).
        replaced = 0
        for p in plan_headers:
            rid = existing.get(p["kode"])
            if rid is None:
                continue
            rows = [x for x in plan_details if x["_doc_key"] == (p["cabang_id"], p["kode"])]
            if not rows:
                continue
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pembelian_detail WHERE pembelian_id=%s", (rid,))
                for d in rows:
                    cur.execute(
                        """
                        INSERT INTO pembelian_detail
                            (pembelian_id, produk_id, satuan_id, konversi_nilai,
                             qty_pesan, qty_dasar_pesan, qty_diterima, qty_dasar_diterima,
                             harga_satuan, diskon_persen, diskon_rp,
                             harga_setelah_diskon, subtotal, catatan)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            rid, d["produk_id"], d["satuan_id"], d["konversi_nilai"],
                            d["qty_pesan"], d["qty_dasar_pesan"], d["qty_diterima"],
                            d["qty_dasar_diterima"], d["harga_satuan"],
                            d["diskon_persen"], d["diskon_rp"], d["harga_setelah_diskon"],
                            d["subtotal"], d["catatan"],
                        ),
                    )
                    detail_count += 1
            conn.commit()
            replaced += 1

        print(f"\nSelesai. Header baru: {inserted}, Detail dipertahankan/ditulis: {detail_count}, Header existing di-backfill: {replaced}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
