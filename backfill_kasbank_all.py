"""Menjalankan 3 tahap migrasi kasbank dalam satu perintah:
  1. backfill_kasbank_masuk.py  (mirror -> kas_bank, kas masuk)
  2. backfill_kasbank_keluar.py (mirror -> kas_bank, kas keluar)
  3. backfill_kasbank_timestamp.py (isi created_at/updated_at/by utk yang belum)

Bulan di-input oleh user (--bulan YYYY-MM). Tanpa --apply = dry-run semua tahap.

Contoh:
  python backfill_kasbank_all.py --env --bulan 2026-01 --cabang-ids 1,2,4,5,7
  python backfill_kasbank_all.py --env --bulan 2026-01 --cabang-ids 1,2,4,5,7 --apply
  python backfill_kasbank_all.py --env --bulan 2026-02 --sampai 2026-02-15 --apply
"""

import argparse
import subprocess
import sys
import time


def _run(label, args):
    print("\n" + "=" * 60)
    print(f"[{label}] python " + " ".join(args))
    print("=" * 60)
    subprocess.run(args, check=True)


def main():
    parser = argparse.ArgumentParser(
        description="Run semua tahap backfill kasbank (masuk -> keluar -> timestamp) sekaligus."
    )
    parser.add_argument("-e", "--env", action="store_true",
                        help="Load config dari .env")
    parser.add_argument("--bulan", default="2026-01",
                        help="Bulan yang dibackfill (YYYY-MM); default 2026-01")
    parser.add_argument("--sampai", default=None,
                        help="Tanggal akhir (YYYY-MM-DD); default = akhir bulan")
    parser.add_argument("--cabang-ids", default="1,2,3,4,5,6,7",
                        help="Comma-separated kasbank_cabang_id; default semua cabang")
    parser.add_argument("--apply", action="store_true",
                        help="Eksekusi & commit. Tanpa flag ini = dry-run.")
    parser.add_argument("--skip-timestamp", action="store_true",
                        help="Lewati tahap 3 (timestamp); hanya migrasi data")
    args = parser.parse_args()

    def _cmd(script):
        cmd = [sys.executable, script]
        if args.env:
            cmd.append("--env")
        return cmd

    # Tahap 1 & 2: terima --bulan / --sampai / --cabang-ids / --apply
    common = ["--bulan", args.bulan]
    if args.sampai:
        common += ["--sampai", args.sampai]
    common += ["--cabang-ids", args.cabang_ids]
    if args.apply:
        common.append("--apply")

    _run("1. KAS MASUK -> kas_bank", _cmd("backfill_kasbank_masuk.py") + common)
    _run("2. KAS KELUAR -> kas_bank", _cmd("backfill_kasbank_keluar.py") + common)

    # Tahap 3: timestamp (script asli tidak menerima --env/--bulan/--cabang-ids;
    #          dia selalu pakai Config.from_env() dan proses semua kas_bank yang
    #          created_at-nya masih NULL).
    if not args.skip_timestamp:
        ts_cmd = [sys.executable, "backfill_kasbank_timestamp.py"]
        if args.apply:
            ts_cmd.append("--apply")
        _run("3. TIMESTAMP created/approved", ts_cmd)

    print("\n" + "=" * 70)
    print("SELESAI. Cek ulang dengan dry-run jika belum --apply.")
    print("=" * 70)


if __name__ == "__main__":
    main()