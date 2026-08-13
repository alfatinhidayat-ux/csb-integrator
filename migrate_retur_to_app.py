"""Migrate historical retur penjualan from Brighter staging tables into the app's
`retur_penjualan` table (csb_db), data-only (no stock/kas_bank/piutang posting).

Approach (as agreed):
- Import ALL retur headers that exist in `brighter_retur_penjualan`.
- Ignore POS invoices: detail lines are only imported when the source invoice
  can be linked to an existing `pos_transactions`/`pos_transaction_items` row
  (via pos_transactions.legacy_id). With `--skip-details`, no detail import.
- kasir_id falls back to an active user of the retur's cabang (mapped via
  karyawan -> authenticated_users); created_by username match attempted first.
- Idempotent: uses client_request_id unique key to skip already-imported retur.

Usage:
    python migrate_retur_to_app.py --dry-run
    python migrate_retur_to_app.py --skip-details
    python migrate_retur_to_app.py --run
"""

from __future__ import annotations

import argparse
import sys
import os

import pymysql

sys.path.insert(0, os.getcwd())

from config import Config
from db import DatabaseManager

STATUS_MAP = {"Tertutup": "posted", "Batal": "cancelled"}
PAYMENT_MAP = {"tunai": "tunai", "transfer": "transfer", "card": "debit"}


def connect_csb() -> pymysql.connections.Connection:
    cfg = Config.from_env()
    kw = cfg.csb_db_kwargs()
    return pymysql.connect(
        host=kw["host"],
        port=kw["port"],
        user=kw["user"],
        password=kw["password"],
        database=kw["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def load_active_users_by_cabang(conn) -> dict[int, list[dict]]:
    """Map cabang_id -> list of active authenticated_users (via karyawan)."""
    users: dict[int, list[dict]] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT k.cabang_id AS cabang_id, u.id AS user_id,
                   u.username, u.name, k.kode_user, k.nama
            FROM karyawan k
            JOIN authenticated_users u ON u.id = k.authenticated_user_id
            WHERE k.aktif = 1 AND u.status = 'active' AND k.cabang_id IS NOT NULL
            ORDER BY k.cabang_id, u.id
            """
        )
        for row in cur.fetchall():
            users.setdefault(int(row["cabang_id"]), []).append(row)
    return users


def pick_kasir(users_by_cabang: dict[int, list[dict]], cabang_id: int, created_by: str | None) -> int:
    """Pick a valid kasir_id for a cabang. Prefer a user matching created_by,
    else the first active user of that cabang."""
    candidates = users_by_cabang.get(int(cabang_id), [])
    if not candidates:
        raise RuntimeError(f"Tidak ada user aktif untuk cabang {cabang_id}")
    needle = (created_by or "").strip().lower()
    if needle:
        for u in candidates:
            haystack = " ".join(
                str(v or "") for v in (u["username"], u["name"], u["kode_user"], u["nama"])
            ).lower()
            if needle in haystack:
                return int(u["user_id"])
    return int(candidates[0]["user_id"])


def load_pos_link(conn) -> dict[int, tuple[int, int]]:
    """Map brighter retur faktur_id (legacy_id) -> (pos_transactions.id, kasir_id)."""
    link: dict[int, tuple[int, int]] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.legacy_id AS legacy_id, p.id AS pos_id, p.kasir_id
            FROM pos_transactions p
            WHERE p.legacy_id IS NOT NULL
            """
        )
        for row in cur.fetchall():
            link[int(row["legacy_id"])] = (int(row["pos_id"]), int(row["kasir_id"]) if row["kasir_id"] else 0)
    return link


def load_retur_headers(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, cabang_id, tanggal, no_bukti, faktur_id, faktur_nobukti,
                   customer_id, status_dokumen, total_rp, keterangan, cara_bayar,
                   cust_no, cust_nama, created_by, created_at
            FROM brighter_retur_penjualan
            ORDER BY cabang_id, id
            """
        )
        return cur.fetchall()


def load_retur_details(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, cabang_id, retur_id, dproduk_id, produk_id, satuan_id,
                   qty, qty_retur, harga, diskon_rp, subtotal_rp,
                   produk_kode, produk_nama, satuan_kode
            FROM brighter_retur_penjualan_detail
            ORDER BY retur_id, id
            """
        )
        return cur.fetchall()


def build_detail_rows(conn, pos_link: dict[int, tuple[int, int]], fill_without_pos: bool = False) -> dict[int, list[dict]]:
    """Map brighter retur id -> list of app retur_penjualan_detail rows.

    A detail line is normally only imported when its parent retur's invoice maps to
    an existing pos_transactions row (matched to pos_transaction_items by product_id).
    When fill_without_pos is True, ALL lines are imported and lines without a POS
    link use placeholder pos_transaction_id/item_id = 0 (requires FOREIGN_KEY_CHECKS=0
    during insert).
    """
    details = load_retur_details(conn)
    if not details:
        return {}

    parent_by_detail: dict[int, int] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, faktur_id FROM brighter_retur_penjualan")
        for row in cur.fetchall():
            parent_by_detail[int(row["id"])] = row["faktur_id"]

    items_by_pos: dict[int, list[dict]] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, pos_transaction_id, product_id, quantity, unit_price, total_price
            FROM pos_transaction_items
            """
        )
        for row in cur.fetchall():
            items_by_pos.setdefault(int(row["pos_transaction_id"]), []).append(row)

    result: dict[int, list[dict]] = {}
    used_items: dict[int, set[int]] = {}
    skipped = 0

    def make_row(d: dict, pos_id, pos_item_id) -> dict:
        prod_id = int(d["produk_id"]) if d["produk_id"] else None
        return {
            "pos_transaction_id": pos_id,
            "pos_transaction_item_id": pos_item_id,
            "produk_id": prod_id,
            "produk_kode": d.get("produk_kode"),
            "produk_nama": d.get("produk_nama") or "",
            "satuan_id": str(d.get("satuan_id") or ""),
            "satuan_label": d.get("satuan_kode") or "",
            "unit_factor": 1,
            "qty_retur": d["qty_retur"] or d["qty"] or 0,
            "qty_dasar_retur": d["qty_retur"] or d["qty"] or 0,
            "harga_satuan": d.get("harga") or 0,
            "subtotal": d.get("subtotal_rp") or 0,
            "stock_ledger_id": None,
        }

    for d in details:
        retur_id = int(d["retur_id"])
        faktur_id = parent_by_detail.get(retur_id)
        pos_id, pos_item_id = 0, 0
        if faktur_id and faktur_id in pos_link:
            pos_id, _ = pos_link[faktur_id]
            pos_items = items_by_pos.get(pos_id, [])
            prod_id = int(d["produk_id"]) if d["produk_id"] else None
            qty = float(d["qty_retur"] or d["qty"] or 0)
            used = used_items.setdefault(pos_id, set())
            match = None
            if prod_id is not None:
                for it in pos_items:
                    if int(it["product_id"]) != prod_id or int(it["id"]) in used:
                        continue
                    if abs(float(it["quantity"] or 0) - qty) < 0.001:
                        match = it
                        break
                if match is None:
                    for it in pos_items:
                        if int(it["product_id"]) == prod_id and int(it["id"]) not in used:
                            match = it
                            break
            if match is not None:
                used.add(int(match["id"]))
                pos_item_id = int(match["id"])

        if pos_id and pos_item_id:
            result.setdefault(retur_id, []).append(make_row(d, pos_id, pos_item_id))
        elif fill_without_pos:
            result.setdefault(retur_id, []).append(make_row(d, 0, 0))
        else:
            skipped += 1

    if skipped:
        print(f"  [detail] {skipped} baris detail dilewati (tidak ada pos_transaction/items).")
    return result


def main():
    parser = argparse.ArgumentParser(description="Migrate brighter retur -> app retur_penjualan (data-only)")
    parser.add_argument("--dry-run", action="store_true", help="Hitung rencana tanpa menulis ke DB")
    parser.add_argument("--skip-details", action="store_true", help="Hanya migrasi header, tanpa detail")
    parser.add_argument("--fill-details-without-pos", action="store_true",
                        help="Isi semua detail meski tanpa pos_transaction (placeholder pos id=0, FK checks dimatikan sementara)")
    parser.add_argument("--yes", action="store_true", help="Langsung tulis ke DB tanpa konfirmasi")
    args = parser.parse_args()

    conn = connect_csb()
    try:
        users_by_cabang = load_active_users_by_cabang(conn)
        pos_link = load_pos_link(conn)
        headers = load_retur_headers(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customer")
            valid_customer_ids = {int(r["id"]) for r in cur.fetchall()}

        detail_rows = {} if args.skip_details else build_detail_rows(conn, pos_link, args.fill_details_without_pos)

        plan = []
        for h in headers:
            kasir_id = pick_kasir(users_by_cabang, h["cabang_id"], h.get("created_by"))
            customer_id = int(h["customer_id"]) if h["customer_id"] is not None else None
            # created_by is a username stored in brighter table? we don't have it here
            plan.append({
                "no_retur": h["no_bukti"],
                "tanggal": str(h["tanggal"]),
                "cabang_id": h["cabang_id"],
                "kasir_id": kasir_id,
                "customer_id": customer_id if customer_id in valid_customer_ids else None,
                "customer_kode": h["cust_no"],
                "customer_nama": h["cust_nama"],
                "faktur_ppn": 1,
                "status": STATUS_MAP.get(h["status_dokumen"], "posted"),
                "keterangan": h["keterangan"],
                "metode_pengembalian": PAYMENT_MAP.get((h["cara_bayar"] or "").lower(), "tunai"),
                "payment_detail": None,
                "total_retur": float(h["total_rp"] or 0),
                "jumlah_pengembalian": float(h["total_rp"] or 0),
                "stock_posted": 0,
                "kas_bank_id": None,
                "client_request_id": f"brighter-retur-{h['id']}",
                "created_by": kasir_id,
                "created_at": str(h["created_at"] or h["tanggal"]),
                "updated_at": str(h["created_at"] or h["tanggal"]),
                "details": detail_rows.get(int(h["id"]), []),
                "_src_id": h["id"],
            })

        total = sum(p["total_retur"] for p in plan)
        custs = len({p["customer_id"] for p in plan})
        print("=" * 60)
        print(f"Rencana migrasi retur penjualan -> csb_db.retur_penjualan")
        print(f"  Header : {len(plan)} retur")
        print(f"  Total  : {total:,.0f}")
        print(f"  Customer: {custs}")
        print(f"  Detail : {sum(len(p['details']) for p in plan)} baris (dari pos_transaction_items yang cocok)")
        print("=" * 60)

        if args.dry_run:
            print("DRY RUN — tidak ada data yang ditulis.")
            return

        # Load existing headers/details for idempotent re-runs (detail backfill)
        with conn.cursor() as cur:
            cur.execute("SELECT id, no_retur FROM retur_penjualan")
            nr_to_id = {r["no_retur"]: int(r["id"]) for r in cur.fetchall()}

        new_headers = [p for p in plan if p["no_retur"] not in nr_to_id]
        existing_headers = [p for p in plan if p["no_retur"] in nr_to_id]
        print(f"  Header baru: {len(new_headers)}, Header sudah ada: {len(existing_headers)}")

        # Confirm before write
        if not args.dry_run and not args.yes:
            ans = input("Tulis ke database? (yes/no): ")
            if ans.strip().lower() != "yes":
                print("Dibatalkan.")
                return

        # Placeholder details (pos id=0) require FOREIGN_KEY_CHECKS disabled briefly.
        need_fk_off = any(d["pos_transaction_id"] == 0 for p in plan for d in p["details"])
        if need_fk_off:
            with conn.cursor() as cur:
                cur.execute("SET FOREIGN_KEY_CHECKS=0")
            print("  [detail] FOREIGN_KEY_CHECKS=0 (sementara, untuk detail placeholder).")

        def insert_detail(cur, retur_id: int, d: dict, ts: str):
            cur.execute(
                """
                INSERT INTO retur_penjualan_detail
                    (retur_penjualan_id, pos_transaction_id, pos_transaction_item_id,
                     produk_id, produk_kode, produk_nama, satuan_id, satuan_label,
                     unit_factor, qty_retur, qty_dasar_retur, harga_satuan, subtotal,
                     stock_ledger_id, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    retur_id, d["pos_transaction_id"], d["pos_transaction_item_id"],
                    d["produk_id"], d["produk_kode"], d["produk_nama"],
                    d["satuan_id"], d["satuan_label"], d["unit_factor"],
                    d["qty_retur"], d["qty_dasar_retur"], d["harga_satuan"],
                    d["subtotal"], d["stock_ledger_id"], ts, ts,
                ),
            )

        inserted = 0
        detail_added = 0
        for p in new_headers:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO retur_penjualan
                        (no_retur, tanggal, cabang_id, kasir_id, customer_id,
                         customer_kode, customer_nama, faktur_ppn, status,
                         keterangan, metode_pengembalian, payment_detail,
                         total_retur, jumlah_pengembalian, stock_posted,
                         kas_bank_id, client_request_id, created_by, created_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        p["no_retur"], p["tanggal"], p["cabang_id"], p["kasir_id"],
                        p["customer_id"], p["customer_kode"], p["customer_nama"],
                        p["faktur_ppn"], p["status"], p["keterangan"],
                        p["metode_pengembalian"], p["payment_detail"],
                        p["total_retur"], p["jumlah_pengembalian"], p["stock_posted"],
                        p["kas_bank_id"], p["client_request_id"], p["created_by"],
                        p["created_at"], p["updated_at"],
                    ),
                )
                retur_id = cur.lastrowid
                for d in p["details"]:
                    insert_detail(cur, retur_id, d, p["created_at"])
                    detail_added += 1
            conn.commit()
            inserted += 1
            if inserted % 25 == 0:
                print(f"  inserted {inserted}/{len(new_headers)}")

        # Backfill detail for headers that already exist (idempotent).
        # Delete that retur's existing detail rows (all of them: these headers are
        # migration-owned and POS links can become real between runs, so keying on
        # pos_transaction_id=0 alone leaves duplicates), then re-insert the full
        # line set so re-runs never accumulate rows.
        for p in existing_headers:
            rid = nr_to_id[p["no_retur"]]
            if not p["details"]:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM retur_penjualan_detail WHERE retur_penjualan_id=%s",
                    (rid,),
                )
                added = 0
                for d in p["details"]:
                    insert_detail(cur, rid, d, p["created_at"])
                    added += 1
            conn.commit()
            detail_added += added

        print(f"\nSelesai. Header baru: {inserted}, Detail ditambahkan: {detail_added}")

        if need_fk_off:
            with conn.cursor() as cur:
                cur.execute("SET FOREIGN_KEY_CHECKS=1")
            print("  FOREIGN_KEY_CHECKS dikembalikan ke 1.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
