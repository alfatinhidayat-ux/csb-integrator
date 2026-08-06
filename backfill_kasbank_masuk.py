"""Backfill kas masuk dari tabel mirror Brighter ke tabel backend (kas_bank + kas_bank_detail).

Migrasi data kas masuk bulan tertentu dari `akuntansi_kasbank_masuk` (mirror, cabang_id=1)
ke tabel baru backend: `kas_bank` (header) + `kas_bank_detail` (baris per komponen).

Pola (mengikuti cara backend):
  - Header -> kas_bank: tipe='masuk', status='approved', no_bukti dari mirror,
    `legacy_kasbank_id` diisi kasbank_id mirror (idempoten).
  - Penerimaan lain -> 1 baris kas_bank_detail per child `_penerimaan_lain`
    JALUR A: penerimaan_id (master_keuangan_penerimaan), akun_lawan_id dari master.
  - Pelunasan piutang karyawan -> 1 baris kas_bank_detail per child `_piutang_karyawan`
    (kas_kategori_id=3 'Pelunasan Piutang Karyawan', akun_lawan_id=10091,
    source_type='pinjaman_karyawan', source_id=ppinjaman_id, karyawan_id, nominal).
  - Pelunasan piutang customer (tanpa child pk) -> kas_kategori_id=2 'Pelunasan Piutang
    Customer', akun_lawan_id=53, source_type='piutang_payment', nominal=kasbank_piutang_bayar.

Catatan: kasbank_masuk_total mirror TIDAK menyertakan kasbank_piutang_bayar. Total header
di-backend = sum seluruh detail (termasuk pelunasan piutang). Tanpa side-effect service,
tidak ada perubahan pinjaman_karyawan (data sudah tertutup di backend).

Default dry-run (menampilkan rencana, tidak commit). Gunakan --apply untuk eksekusi.
"""
import argparse
import json
import os
import sys

import pymysql

sys.path.insert(0, os.getcwd())
from config import Config

KAS_TUNAI_KASIR_KODE = "1.11.04"

KAT_PENERIMAAN_LAIN = 6      # Penerimaan Lain-Lain (jalur B fallback)
KAT_PIUTANG_KARYAWAN = 3     # Pelunasan Piutang Karyawan (akun 10091)
KAT_PIUTANG_CUSTOMER = 2     # Pelunasan Piutang Customer (akun 53)

AKUN_PIUTANG_KARYAWAN = 10091
AKUN_PIUTANG_CUSTOMER = 53


def _dec(v):
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _main():
    parser = argparse.ArgumentParser(
        description="Backfill kas masuk mirror -> tabel backend kas_bank/kas_bank_detail")
    parser.add_argument("-e", "--env", action="store_true")
    parser.add_argument("--bulan", default="2026-01",
                        help="Bulan yang dibackfill (YYYY-MM); default 2026-01")
    parser.add_argument("--sampai", default=None,
                        help="Tanggal akhir (YYYY-MM-DD); default = akhir bulan")
    parser.add_argument("--cabang-ids", default="1",
                        help="Comma-separated kasbank_cabang_id; default 1 (SB)")
    parser.add_argument("--apply", action="store_true",
                        help="Eksekusi & commit. Tanpa flag ini = dry-run.")
    args = parser.parse_args()

    config = Config.from_env()
    kw = config.csb_db_kwargs()
    conn = pymysql.connect(**kw, cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4")
    cur = conn.cursor()

    y, m = args.bulan.split("-")
    date_from = f"{y}-{m}-01"
    last_day = 31
    if m in ("04", "06", "09", "11"):
        last_day = 30
    elif m == "02":
        last_day = 28
    date_to = f"{y}-{m}-{last_day}"
    if args.sampai:
        date_to = args.sampai

    cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]
    cabang_filter = "(%s)" % ",".join(str(x) for x in cabang_ids)

    cur.execute(
        "SELECT akun_id, cabang_id, akun_nama, akun_kode FROM akun_cashbank "
        "WHERE cabang_id IN %s AND akun_kode=%s" % (cabang_filter, "%s"),
        (KAS_TUNAI_KASIR_KODE,))
    akun_by_cabang = {r["cabang_id"]: r for r in cur.fetchall()}

    cur.execute("SELECT id FROM karyawan")
    valid_karyawan = {r["id"] for r in cur.fetchall()}

    cur.execute("SELECT id, nama, kode FROM cabang WHERE id IN %s" % cabang_filter)
    cabang_nama = {r["id"]: r["nama"] for r in cur.fetchall()}

    cur.execute(
        "SELECT km.* FROM akuntansi_kasbank_masuk km "
        "WHERE km.kasbank_tanggal BETWEEN %s AND %s AND km.kasbank_cabang_id IN %s "
        "ORDER BY km.kasbank_tanggal, km.kasbank_nobukti".replace(
            " IN %s ", " IN " + cabang_filter),
        (date_from, date_to))
    headers = cur.fetchall()

    # Child penerimaan lain per header
    cur.execute(
        "SELECT pl.kasbank_id, pl.dkasbank_penerimaan_penerimaan_id penerimaan_id, "
        "pl.dkasbank_penerimaan_akun_id akun_id, pl.dkasbank_penerimaan_karyawan_id kid, "
        "pl.dkasbank_penerimaan_rp rp, pl.dkasbank_penerimaan_ket ket "
        "FROM akuntansi_kasbank_masuk_penerimaan_lain pl "
        "WHERE pl.kasbank_id IN (SELECT kasbank_id FROM akuntansi_kasbank_masuk "
        " WHERE kasbank_tanggal BETWEEN %s AND %s AND kasbank_cabang_id IN %s)".replace(
            " IN %s)", " IN " + cabang_filter + ")"),
        (date_from, date_to))
    pl_child = {}
    for r in cur.fetchall():
        pl_child.setdefault(r["kasbank_id"], []).append(r)

    # Child piutang karyawan per header
    cur.execute(
        "SELECT pk.kasbank_id, pk.kdpk_ppinjaman_id ppinjaman_id, pk.kdpk_dilunasi dilunasi "
        "FROM akuntansi_kasbank_masuk_piutang_karyawan pk "
        "WHERE pk.kasbank_id IN (SELECT kasbank_id FROM akuntansi_kasbank_masuk "
        " WHERE kasbank_tanggal BETWEEN %s AND %s AND kasbank_cabang_id IN %s)".replace(
            " IN %s)", " IN " + cabang_filter + ")"),
        (date_from, date_to))
    pk_child = {}
    for r in cur.fetchall():
        pk_child.setdefault(r["kasbank_id"], []).append(r)

    # Map karyawan per pinjaman (backend) utk pelunasan piutang karyawan.
    # kdpk_ppinjaman_id di mirror bertipe TEXT -> cast ke int untuk lookup.
    all_ppin = {int(r["ppinjaman_id"]) for ch in pk_child.values() for r in ch if r["ppinjaman_id"]}
    if all_ppin:
        cur.execute("SELECT ppinjaman_id, ppinjaman_karyawan_id FROM pinjaman_karyawan "
                    "WHERE ppinjaman_id IN (%s)" % ",".join(str(x) for x in all_ppin))
        karyawan_by_pinjaman = {int(r["ppinjaman_id"]): r["ppinjaman_karyawan_id"] for r in cur.fetchall()}
    else:
        karyawan_by_pinjaman = {}

    # Header yang sudah pernah dimigrasi (idempotensi)
    cur.execute("SELECT legacy_kasbank_id FROM kas_bank WHERE legacy_kasbank_id IS NOT NULL")
    done = {r["legacy_kasbank_id"] for r in cur.fetchall()}

    print("=" * 120)
    print("BACKFILL KAS MASUK %s | cabang=%s | mode=%s" % (
        args.bulan, args.cabang_ids, "APPLY (commit)" if args.apply else "DRY-RUN (no commit)"))
    print("=" * 120)

    missing_akun = [c for c in cabang_ids if c not in akun_by_cabang]
    if missing_akun:
        print("!! AKUN CASHBANK 'Kas Tunai Kasir' tidak ditemukan utk cabang:", missing_akun)

    total_kb = 0
    total_detail = 0
    total_rp = 0.0
    skipped = 0

    for h in headers:
        kb_id = h["kasbank_id"]
        if kb_id in done:
            skipped += 1
            continue

        cbg = int(h["kasbank_cabang_id"])
        akun = akun_by_cabang.get(cbg)
        if not akun:
            print("  !! SKIP %s: akun cashbank cbg %s tidak tersedia" % (h["kasbank_nobukti"], cbg))
            continue

        header = {
            "no_bukti": h["kasbank_nobukti"],
            "tipe": "masuk",
            "jenis_transaksi": "kas_masuk",
            "akun_cashbank_id": akun["akun_id"],
            "cabang_id": cbg,
            "tanggal": str(h["kasbank_tanggal"]),
            "keterangan": h["kasbank_keterangan"],
            "total_nominal": 0.0,
            "status": "approved",
            "approved_by": "system",
            "approved_at": None,
            "akun_nama_snapshot": akun["akun_nama"],
            "akun_kode_snapshot": akun["akun_kode"],
            "cabang_nama_snapshot": cabang_nama.get(cbg),
            "legacy_kasbank_id": str(kb_id),
            "created_by": "system",
        }

        details = []
        komponen = []

        # 1) Penerimaan lain -> JALUR A (penerimaan_id dari master_keuangan_penerimaan)
        for pl in pl_child.get(kb_id, []):
            pid = pl["penerimaan_id"]
            kid = pl["kid"]
            kf = None
            if kid:
                try:
                    kint = int(kid)
                except (TypeError, ValueError):
                    kint = None
                if kint in valid_karyawan:
                    kf = kint
            details.append({
                "penerimaan_id": pid if pid else None,
                "kas_kategori_id": None,
                "akun_lawan_id": pl["akun_id"] or AKUN_PIUTANG_KARYAWAN,
                "source_type": None,
                "source_id": None,
                "karyawan_id": kf,
                "nominal": _dec(pl["rp"]),
                "keterangan_detail": pl["ket"],
            })
        if kb_id in pl_child:
            komponen.append("pl")

        # 2) Pelunasan piutang karyawan -> kas_kategori 3
        for pk in pk_child.get(kb_id, []):
            pid = int(pk["ppinjaman_id"]) if pk["ppinjaman_id"] else None
            kid = karyawan_by_pinjaman.get(pid)
            details.append({
                "penerimaan_id": None,
                "kas_kategori_id": KAT_PIUTANG_KARYAWAN,
                "akun_lawan_id": AKUN_PIUTANG_KARYAWAN,
                "source_type": "pinjaman_karyawan",
                "source_id": pid if pid else None,
                "karyawan_id": kid if kid else None,
                "nominal": _dec(pk["dilunasi"]),
                "keterangan_detail": None,
            })
        if kb_id in pk_child:
            komponen.append("pk")

        # 3) Pelunasan piutang customer (piutang_bayar tanpa child pk)
        pb = _dec(h["kasbank_piutang_bayar"])
        if pb > 0 and kb_id not in pk_child:
            details.append({
                "penerimaan_id": None,
                "kas_kategori_id": KAT_PIUTANG_CUSTOMER,
                "akun_lawan_id": AKUN_PIUTANG_CUSTOMER,
                "source_type": "piutang_payment",
                "source_id": None,
                "karyawan_id": None,
                "nominal": pb,
                "keterangan_detail": None,
            })
            komponen.append("pb_cust")

        # 4) Sisa total yang belum tertutup child -> fallback penerimaan lain
        #    (child mirror tidak selalu lengkap; total header mirror = sumber kebenaran)
        mirror_total = _dec(h["kasbank_masuk_total"]) + pb
        sum_detail = sum(_dec(d["nominal"]) for d in details)
        sisa = mirror_total - sum_detail
        if sisa > 0.01:
            first_pl = (pl_child.get(kb_id) or [{}])[0]
            details.append({
                "penerimaan_id": None,
                "kas_kategori_id": KAT_PENERIMAAN_LAIN,
                "akun_lawan_id": _dec(first_pl.get("akun_id")) if first_pl.get("akun_id") else 2072,
                "source_type": None,
                "source_id": None,
                "karyawan_id": None,
                "nominal": sisa,
                "keterangan_detail": "Penerimaan lain (backfill)",
            })
            komponen.append("pl_fallback")

        sum_detail = sum(_dec(d["nominal"]) for d in details)
        total_hdr = sum_detail
        header["total_nominal"] = total_hdr
        flag = "" if abs(total_hdr - mirror_total) < 0.01 else "  <-- komponen != mirror!"

        if args.apply:
            cur.execute(
                "INSERT INTO kas_bank (no_bukti, tipe, jenis_transaksi, akun_cashbank_id, "
                "cabang_id, tanggal, keterangan, total_nominal, status, approved_by, approved_at, "
                "akun_nama_snapshot, akun_kode_snapshot, cabang_nama_snapshot, legacy_kasbank_id, "
                "created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (header["no_bukti"], header["tipe"], header["jenis_transaksi"],
                 header["akun_cashbank_id"], header["cabang_id"], header["tanggal"],
                 header["keterangan"], header["total_nominal"], header["status"],
                 header["approved_by"], header["approved_at"],
                 header["akun_nama_snapshot"], header["akun_kode_snapshot"],
                 header["cabang_nama_snapshot"], header["legacy_kasbank_id"], header["created_by"]))
            new_kb_id = cur.lastrowid

            for d in details:
                cur.execute(
                    "INSERT INTO kas_bank_detail (kas_bank_id, penerimaan_id, kas_kategori_id, "
                    "akun_lawan_id, source_type, source_id, karyawan_id, nominal, keterangan_detail) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (new_kb_id, d["penerimaan_id"], d["kas_kategori_id"], d["akun_lawan_id"],
                     d["source_type"], d["source_id"], d["karyawan_id"], d["nominal"],
                     d["keterangan_detail"]))

        total_kb += 1
        total_detail += len(details)
        total_rp += total_hdr

        print("  %-4s %-18s %-10s %14d rp  %-20s %s" % (
            cbg, h["kasbank_nobukti"], h["kasbank_tanggal"], int(total_hdr),
            "|".join(komponen) if komponen else "-", flag))

    print("-" * 120)
    print("RINGKASAN: %d header baru, %d detail, Rp %s total" % (
        total_kb, total_detail, f"{total_rp:,.0f}"))
    print("SKIP (sudah dimigrasi): %d" % skipped)

    if not args.apply:
        print("DRY-RUN selesai — tidak ada perubahan. Jalankan dengan --apply untuk eksekusi.")
    else:
        conn.commit()
        print("APPLY selesai — data telah di-commit ke DB.")


if __name__ == "__main__":
    _main()
