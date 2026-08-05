"""Satu perintah: jalankan ulang pipeline keuangan tanpa duplikasi.

Fokus pada data yang relevan UNTUK REKON & FINANCE (bukan full mirror — tidak
termasuk produk, karyawan, master, persediaan, penjualan, dll).

Jalankan:
    python run_all_sync.py --env [--cabang-ids 1,4] [--verbose]

Urutan (setiap tabel hanya diproses SATU kali, tidak ada tumpang tindih):
  1. python sync_finance.py --env      -> csb_db: supplier, pelunasan hutang/piutang,
                                           faktur pembelian
  2. python sync_rekon_pinjaman.py     -> kasbank-only (kas/bank csb) + sync_pinjaman.py
       --env                            + rekon_pinjaman_karyawan.py

Catatan: tidak menjalankan main.py run_all (won't sync produk/karyawan/master).
sync_pos / sync_csb_produk / sync_coretax / customer_sync juga TIDAK dimasukkan.
"""
import argparse
import os
import subprocess
import sys

PY = sys.executable
VERBOSE = False


def run(step, title, script, flags=(), check=True):
    print("\n" + "=" * 70)
    print(f"[{step}] {title}")
    print("=" * 70)
    cmd = [PY, script] + list(flags) + extra_args + ["--env"]
    if VERBOSE:
        cmd.append("--verbose")
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.run(cmd, env=env)
    if proc.returncode != 0 and check:
        print(f"\n[gagal] langkah {step} gagal (exit {proc.returncode}). Berhenti.")
        sys.exit(proc.returncode)
    return proc.returncode


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Jalankan ulang pipeline keuangan (finance + kasbank + pinjaman + rekon), tanpa mirror"
    )
    parser.add_argument("-e", "--env", action="store_true",
                        help="Load config dari environment (BRIGHTER_*)")
    parser.add_argument("--cabang-ids", default=None,
                        help="Comma-separated cabang IDs; default semua aktif")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE = bool(args.verbose)

    extra_args = []
    if args.cabang_ids:
        extra_args += ["--cabang-ids", args.cabang_ids]
    if args.verbose:
        extra_args.append("--verbose")

    run(1, "Finance: supplier + pelunasan hutang/piutang + faktur pembelian", "sync_finance.py",
        check=True)
    run(2, "Kas/bank + pinjaman + rekon", "sync_rekon_pinjaman.py", check=True)

    print("\n" + "=" * 70)
    print("SELESAI: pipeline keuangan dijalankan ulang. Periksa log tiap langkah di atas.")
    print("=" * 70)