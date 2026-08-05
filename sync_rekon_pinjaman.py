"""Satu perintah: clear + re-sync kas/bank, sync pinjaman_karyawan, lalu rekon.

Jalankan:
    python sync_rekon_pinjaman.py --env [--cabang-ids 1,4] [--verbose]

Urutan:
  1. python main.py --env --kasbank-only       (truncate + full re-sync akuntansi_kasbank_*)
  2. python sync_pinjaman.py --env             (upsert pinjaman_karyawan, clamp sisa negatif)
  3. python rekon_pinjaman_karyawan.py --env   (laporan konsistensi, read-only)

Gagal di langkah 1/2 => berhenti dengan exit code != 0. Rekon tetap dijalankan
walau ada langkah yang gagal, supaya tetap terlihat kondisi datanya.
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
    # rekon_pinjaman_karyawan.py tidak mendukung flag --verbose
    supports_verbose = not script.startswith("rekon_pinjaman")
    if VERBOSE and supports_verbose:
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
        description="Clear + re-sync kasbank & pinjaman karyawan, lalu rekon konsistensi"
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

    run(1, "Clear + re-sync kas/bank (kasbank-only)", "main.py",
        flags=("--kasbank-only",), check=True)
    run(2, "Sync pinjaman_karyawan", "sync_pinjaman.py", check=True)
    rc = run(3, "Rekonsiliasi pinjaman vs kas/bank", "rekon_pinjaman_karyawan.py", check=False)

    print("\n" + "=" * 70)
    if rc == 0:
        print("SELESAI: kasbank & pinjaman tersinkron, cek laporan rekon di atas.")
    else:
        print("SELESAI dengan catatan: ada langkah yang gagal / rekon menemukan selisih — periksa di atas.")
    print("=" * 70)