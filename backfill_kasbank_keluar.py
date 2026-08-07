"""Backfill kas keluar dari tabel mirror Brighter ke tabel backend (kas_bank + kas_bank_detail).

Migrasi data kas keluar bulan tertentu dari `akuntansi_kasbank_keluar` (mirror, cabang_id=1)
ke tabel baru backend: `kas_bank` (header) + `kas_bank_detail` (baris per komponen).

Pola (mengikuti cara backend):
  - Header -> kas_bank: tipe='keluar', status='approved', no_bukti dari mirror,
    `legacy_kasbank_id` diisi kasbank_id mirror (idempoten).
  - Pinjaman karyawan -> 1 baris kas_bank_detail per child `_detail_pinjaman_karyawan`
    (kas_kategori_id=9, akun_lawan_id=10091, source_type='pengajuan_dana_karyawan',
    source_id=ppinjaman_id, karyawan_id, nominal).
  - Pengeluaran lain -> 1 baris kas_bank_detail AGREGAT per header
    (kas_kategori_id=10, akun_lawan_id=2072, nominal=kasbank_pengeluaran_lain)
    karena child pengeluaran_lain di mirror tidak lengkap.
  - Gaji karyawan -> 1 baris agregat (kas_kategori_id=8, akun_lawan_id=10127).
  - Link balik: pinjaman_karyawan.ppinjaman_dkasbank_pinjaman_karyawan_id = kas_bank.id.

Default dry-run (menampilkan rencana, tidak commit). Gunakan --apply untuk eksekusi.
Tidak melewati KasBankService::approve() sehingga TIDAK membuat loan baru (tanpa duplikat).
"""
import argparse
import os
import sys

import pymysql

sys.path.insert(0, os.getcwd())
from config import Config

# Pemetaan kasbank_akun (mirror) -> akun_cashbank per cabang ("Kas Tunai Kasir", kode 1.11.04)
KAS_TUNAI_KASIR_KODE = "1.11.04"

# Default akun lawan per komponen (diambil dari kas_kategori backend)
AKUN_PINJAMAN = 10091      # Piutang Karyawan
AKUN_PENGELUARAN_LAIN = 2072  # Beban Lain - Lain
AKUN_GAJI = 10127          # Utang Gaji (kas_kategori 8)


def _dec(v):
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def resolve_nobukti_collision(cur, no_formatting, apply=False):
    cur.execute("SELECT id, legacy_kasbank_id FROM kas_bank WHERE no_formatting = %s".replace("no_formatting", "no_bukti"), (no_formatting,))
    row = cur.fetchone()
    if not row:
        return "insert"
    
    if row["legacy_kasbank_id"] is not None:
        return "skip"
        
    if "-" in no_formatting:
        parts = no_formatting.rsplit("-", 1)
        prefix = parts[0] + "-"
        suffix = parts[1]
        suffix_len = len(suffix)
    else:
        prefix = no_formatting + "-"
        suffix_len = 4
        
    cur.execute("SELECT no_formatting FROM kas_bank WHERE no_formatting LIKE %s".replace("no_formatting", "no_bukti"), (prefix + "%",))
    existing_nobuktis = [r["no_bukti"] for r in cur.fetchall()]
    
    max_num = 0
    for eb in existing_nobuktis:
        if eb.startswith(prefix):
            num_part = eb[len(prefix):]
            num_digits = "".join(c for c in num_part if c.isdigit())
            if num_digits:
                try:
                    max_num = max(max_num, int(num_digits))
                except ValueError:
                    pass
                    
    next_num = max_num + 1
    new_no_built = f"{prefix}{str(next_num).zfill(suffix_len)}"
    
    if apply:
        cur.execute("UPDATE kas_bank SET no_formatting = %s WHERE id = %s".replace("no_formatting", "no_bukti"), (new_no_built, row["id"]))
        print(f"  [COLLISION RESOLVED] Renamed existing app record ID {row['id']} from '{no_formatting}' to '{new_no_built}'")
    else:
        print(f"  [COLLISION WILL RESOLVE] Will rename existing app record ID {row['id']} from '{no_formatting}' to '{new_no_built}'")
        
    return "insert"


def _main():
    parser = argparse.ArgumentParser(
        description="Backfill kas keluar mirror -> tabel backend kas_bank/kas_bank_detail")
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

    # Bulan pertama & terakhir
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

    # Ambil semua akun cashbank untuk cabang-cabang ini
    cur.execute(
        "SELECT akun_id, cabang_id, akun_nama, akun_kode FROM akun_cashbank "
        "WHERE cabang_id IN %s" % cabang_filter)
    akun_by_id = {r["akun_id"]: r for r in cur.fetchall()}

    # Cek akun lawan ada
    cur.execute("SELECT akun_id FROM akun WHERE akun_id IN (%s)" %
                ",".join(str(x) for x in (AKUN_PINJAMAN, AKUN_PENGELUARAN_LAIN, AKUN_GAJI)))
    akun_ok = {r["akun_id"] for r in cur.fetchall()}

    cur.execute("SELECT id, nama, kode FROM cabang WHERE id IN %s" % cabang_filter)
    cabang_nama = {r["id"]: r["nama"] for r in cur.fetchall()}

    cur.execute("SELECT id FROM karyawan")
    valid_karyawan = {r["id"] for r in cur.fetchall()}

    # Header kas keluar periode + cabang
    cur.execute(
        "SELECT kk.* FROM akuntansi_kasbank_keluar kk "
        "WHERE kk.kasbank_tanggal BETWEEN %s AND %s AND kk.kasbank_cabang_id IN %s "
        "ORDER BY kk.kasbank_tanggal, kk.kasbank_nobukti".replace(
            " IN %s ", " IN " + cabang_filter),
        (date_from, date_to))
    headers = cur.fetchall()

    # Child pinjaman per header
    cur.execute(
        "SELECT d.kasbank_id, d.dkasbank_pinjaman_ppinjaman_id pid, "
        "d.dkasbank_pinjaman_karyawan_id kid, d.dkasbank_pinjaman_rp rp, d.dkasbank_pinjaman_ket ket "
        "FROM akuntansi_kasbank_keluar_detail_pinjaman_karyawan d "
        "WHERE d.kasbank_id IN (SELECT kasbank_id FROM akuntansi_kasbank_keluar "
        " WHERE kasbank_tanggal BETWEEN %s AND %s AND kasbank_cabang_id IN %s)".replace(
            " IN %s)", " IN " + cabang_filter + ")"),
        (date_from, date_to))
    pk_child = {}
    for r in cur.fetchall():
        pk_child.setdefault(r["kasbank_id"], []).append(r)

    # Child pengeluaran lain per header (klasifikasi asli dari pengeluaran_id)
    cur.execute(
        "SELECT pl.kasbank_id, pl.dkasbank_pengeluaran_pengeluaran_id pid, "
        "pl.dkasbank_pengeluaran_rp rp, pl.dkasbank_pengeluaran_ket ket "
        "FROM akuntansi_kasbank_keluar_pengeluaran_lain pl "
        "WHERE pl.kasbank_id IN (SELECT kasbank_id FROM akuntansi_kasbank_keluar "
        " WHERE kasbank_tanggal BETWEEN %s AND %s AND kasbank_cabang_id IN %s)".replace(
            " IN %s)", " IN " + cabang_filter + ")"),
        (date_from, date_to))
    pl_child = {}
    for r in cur.fetchall():
        pl_child.setdefault(r["kasbank_id"], []).append(r)

    # Jurnal/akun tujuan per header (untuk transaksi transfer/setor bank)
    cur.execute(
        "SELECT kasbank_id, dkasbank_akun, dkasbank_debet FROM akuntansi_kasbank_keluar_item "
        "WHERE kasbank_id IN (SELECT kasbank_id FROM akuntansi_kasbank_keluar "
        " WHERE kasbank_tanggal BETWEEN %s AND %s AND kasbank_cabang_id IN %s)".replace(
            " IN %s)", " IN " + cabang_filter + ")"),
        (date_from, date_to))
    item_child = {}
    for r in cur.fetchall():
        item_child.setdefault(r["kasbank_id"], []).append(r)

    # Klasifikasi akun tujuan (bank = kode 1.12; selain itu = pengeluaran lain)
    cur.execute(
        "SELECT akun_id, akun_kode FROM akun WHERE akun_id IN (SELECT dkasbank_akun "
        "FROM akuntansi_kasbank_keluar_item WHERE dkasbank_akun IS NOT NULL)")
    akun_kode_by_id = {r["akun_id"]: (r["akun_kode"] or "") for r in cur.fetchall()}

    # Header yang sudah pernah dimigrasi (idempotensi)
    cur.execute("SELECT legacy_kasbank_id FROM kas_bank WHERE legacy_kasbank_id IS NOT NULL")
    done = {r["legacy_kasbank_id"] for r in cur.fetchall()}

    print("=" * 120)
    print("BACKFILL KAS KELUAR %s | cabang=%s | mode=%s" % (
        args.bulan, args.cabang_ids, "APPLY (commit)" if args.apply else "DRY-RUN (no commit)"))
    print("=" * 120)

    cabang_with_akun = {r["cabang_id"] for r in akun_by_id.values()}
    missing_akun = [c for c in cabang_ids if c not in cabang_with_akun]
    if missing_akun:
        print("!! AKUN CASHBANK tidak ditemukan utk cabang:", missing_akun)
    if akun_ok != {AKUN_PINJAMAN, AKUN_PENGELUARAN_LAIN, AKUN_GAJI}:
        print("!! Akun lawan tidak lengkap:", akun_ok)

    total_kb = 0
    total_detail = 0
    total_rp = 0.0
    skipped = 0

    for h in headers:
        kb_id = h["kasbank_id"]
        nobukti = h["kasbank_nobukti"]
        
        # Check and resolve collisions automatically
        collision_action = resolve_nobukti_collision(cur, nobukti, args.apply)
        if collision_action == "skip" or kb_id in done:
            skipped += 1
            continue
        if _dec(h["kasbank_keluar_total"]) <= 0:
            skipped += 1
            continue

        cbg = int(h["kasbank_cabang_id"])
        
        # Get actual account dynamically from kasbank_akun
        ref_akun_id = None
        if h.get("kasbank_akun"):
            try:
                ref_akun_id = int(h["kasbank_akun"])
            except (ValueError, TypeError):
                pass

        akun = None
        if ref_akun_id and ref_akun_id in akun_by_id:
            akun = akun_by_id[ref_akun_id]
        else:
            # Fallback to Kas Tunai Kasir ("1.11.04")
            for a in akun_by_id.values():
                if a["cabang_id"] == cbg and a["akun_code"] == "1.11.04":
                    akun = a
                    break
            if not akun:
                for a in akun_by_id.values():
                    if a["cabang_id"] == cbg:
                        akun = a
                        break

        if not akun:
            print("  !! SKIP %s: akun cashbank cbg %s tidak tersedia" % (h["kasbank_nobukti"], cbg))
            continue

        # --- Header kas_bank ---
        header = {
            "no_bukti": h["kasbank_nobukti"],
            "tipe": "keluar",
            "jenis_transaksi": "kas_keluar",
            "akun_cashbank_id": akun["akun_id"],
            "cabang_id": cbg,
            "tanggal": str(h["kasbank_tanggal"]),
            "keterangan": h["kasbank_keterangan"],
            "total_nominal": _dec(h["kasbank_keluar_total"]),
            "status": "approved",
            "approved_by": "system",
            "approved_at": None,
            "akun_nama_snapshot": akun["akun_nama"],
            "akun_kode_snapshot": akun["akun_kode"],
            "cabang_nama_snapshot": cabang_nama.get(cbg),
            "legacy_kasbank_id": str(kb_id),
            "created_by": "system",
        }

        # --- Detail ---
        details = []
        # 1) Pinjaman karyawan (dari child, akurat)
        for c in pk_child.get(kb_id, []):
            kf = None
            if c["kid"]:
                try:
                    kint = int(c["kid"])
                except (TypeError, ValueError):
                    kint = None
                if kint in valid_karyawan:
                    kf = kint
            details.append({
                "kas_kategori_id": 9,
                "akun_lawan_id": AKUN_PINJAMAN,
                "source_type": "pengajuan_dana_karyawan",
                "source_id": c["pid"],
                "karyawan_id": kf,
                "nominal": _dec(c["rp"]),
                "keterangan_detail": c["ket"],
            })
        # 2) Pengeluaran lain -> per child `_pengeluaran_lain` bila ada.
        #    Klasifikasi dari pengeluaran_id (master_keuangan_pengeluaran):
        #    - '9' (GAJI KARYAWAN) -> kategori 8 Pembayaran Gaji Karyawan
        #    - lainnya -> kategori 10 Pengeluaran Lain-Lain
        pl_children = pl_child.get(kb_id, [])
        pl_child_sum = sum(_dec(x["rp"]) for x in pl_children)
        for x in pl_children:
            is_gaji = str(x["pid"]) == "9"
            details.append({
                "kas_kategori_id": 8 if is_gaji else 10,
                "akun_lawan_id": AKUN_GAJI if is_gaji else AKUN_PENGELUARAN_LAIN,
                "source_type": "gaji_karyawan" if is_gaji else None,
                "source_id": None,
                "karyawan_id": None,
                "nominal": _dec(x["rp"]),
                "keterangan_detail": x["ket"] or (
                    "Pembayaran gaji (backfill)" if is_gaji else "Pengeluaran lain (backfill)"),
            })
        # 2b) Sisa pengeluaran lain yg tak tertutup child (child mirror tidak lengkap)
        pl = _dec(h["kasbank_pengeluaran_lain"])
        if pl - pl_child_sum > 0.01:
            details.append({
                "kas_kategori_id": 10,
                "akun_lawan_id": AKUN_PENGELUARAN_LAIN,
                "source_type": None,
                "source_id": None,
                "karyawan_id": None,
                "nominal": pl - pl_child_sum,
                "keterangan_detail": "Pengeluaran lain (backfill)",
            })
        # 3) Gaji karyawan (agregat header)
        gaji = _dec(h["kasbank_pengeluaran_gaji_karyawan"])
        if gaji > 0:
            details.append({
                "kas_kategori_id": 8,
                "akun_lawan_id": AKUN_GAJI,
                "source_type": "gaji_karyawan",
                "source_id": None,
                "karyawan_id": None,
                "nominal": gaji,
                "keterangan_detail": "Pembayaran gaji (backfill)",
            })

        # 4) Sisa total yang belum tertutup komponen -> transfer/setor bank.
        #    Pakai akun tujuan dari jurnal item. Akun bank (kode 1.12) -> kategori
        #    setor_bank; selain itu dianggap pengeluaran lain.
        sum_comp = sum(_dec(d["nominal"]) for d in details)
        sisa = _dec(h["kasbank_keluar_total"]) - sum_comp
        if sisa > 0.01:
            items = item_child.get(kb_id, [])
            akun_tujuan = None
            for it in items:
                if _dec(it["dkasbank_debet"]) > 0:
                    akun_tujuan = it["dkasbank_akun"]
                    break
            if akun_tujuan:
                kode = akun_kode_by_id.get(int(akun_tujuan), "")
                if kode.startswith("1.12"):
                    details.append({
                        "kas_kategori_id": 13,
                        "akun_lawan_id": int(akun_tujuan),
                        "source_type": "setor_bank",
                        "source_id": None,
                        "karyawan_id": None,
                        "nominal": sisa,
                        "keterangan_detail": "Setor bank (backfill)",
                    })
                else:
                    details.append({
                        "kas_kategori_id": 10,
                        "akun_lawan_id": AKUN_PENGELUARAN_LAIN,
                        "source_type": None,
                        "source_id": None,
                        "karyawan_id": None,
                        "nominal": sisa,
                        "keterangan_detail": "Pengeluaran lain (backfill)",
                    })
            else:
                details.append({
                    "kas_kategori_id": 10,
                    "akun_lawan_id": AKUN_PENGELUARAN_LAIN,
                    "source_type": None,
                    "source_id": None,
                    "karyawan_id": None,
                    "nominal": sisa,
                    "keterangan_detail": "Pengeluaran lain (backfill)",
                })

        sum_detail = sum(_dec(d["nominal"]) for d in details)
        total_hdr = _dec(h["kasbank_keluar_total"])
        flag = "" if abs(sum_detail - total_hdr) < 0.01 else "  <-- TOTAL DETAIL != HEADER!"

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
                    "INSERT INTO kas_bank_detail (kas_bank_id, kas_kategori_id, akun_lawan_id, "
                    "source_type, source_id, karyawan_id, nominal, keterangan_detail) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (new_kb_id, d["kas_kategori_id"], d["akun_lawan_id"], d["source_type"],
                     d["source_id"], d["karyawan_id"], d["nominal"], d["keterangan_detail"]))

            # Link balik pinjaman -> kas keluar
            pids = [c["pid"] for c in pk_child.get(kb_id, []) if c["pid"]]
            for pid in pids:
                cur.execute(
                    "UPDATE pinjaman_karyawan SET ppinjaman_dkasbank_pinjaman_karyawan_id=%s "
                    "WHERE ppinjaman_id=%s", (new_kb_id, pid))

        total_kb += 1
        total_detail += len(details)
        total_rp += total_hdr

        komponen = []
        if len(pk_child.get(kb_id, [])):
            komponen.append("pk:%d" % len(pk_child[kb_id]))
        if pl > 0:
            komponen.append("pl")
        if gaji > 0:
            komponen.append("gaji")
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
