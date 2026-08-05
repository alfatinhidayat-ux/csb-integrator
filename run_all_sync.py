"""Satu perintah: jalankan ulang SELURUH pipeline sync tanpa duplikasi.

Jalankan:
    python run_all_sync.py --env [--cabang-ids 1,4] [--verbose]

Urutan (setiap tabel hanya diproses SATU kali, tidak ada tumpang tindih):
  1. python main.py --env              -> full mirror 62 endpoint inti (brighter_mirror +
                                           csb: users/karyawan), dgn clean_start
  2. python sync_finance.py --env      -> csb_db: supplier, pelunasan hutang/piutang,
                                           faktur pembelian
  3. python sync_rekon_pinjaman.py     -> kasbank-only (kas/bank csb) + sync_pinjaman.py
       --env                            + rekon_pinjaman_karyawan.py
                                          (kasbank & pinjaman TIDAK dijalankan ulang di sini,
                                           sudah tunggal lewat orchestrator ini)

Catatan: sync_pos / sync_csb_produk / sync_coretax / customer_sync TIDAK dimasukkan
(default). Tambahkan sendiri jika diperlukan.
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
        description="Jalankan ulang seluruh pipeline sync (mirror + finance + kasbank + pinjaman + rekon)"
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

    run(1, "Full mirror 62 endpoint (brighter_mirror + users/karyawan csb)", "main.py",
        check=True)
    run(2, "Finance: supplier + pelunasan hutang/piutang + faktur pembelian", "sync_finance.py",
        check=True)
    run(3, "Kas/bank + pinjaman + rekon", "sync_rekon_pinjaman.py", check=True)

    print("\n" + "=" * 70)
    print("SELESAI: seluruh pipeline dijalankan ulang. Periksa log tiap langkah di atas.")
    print("=" * 70)