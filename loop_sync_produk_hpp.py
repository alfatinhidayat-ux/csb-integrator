import argparse
import calendar
import subprocess
import sys
from datetime import date


def month_range(year: int, month: int) -> tuple[str, str]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1).isoformat(), date(year, month, last).isoformat()


def main():
    parser = argparse.ArgumentParser(
        description="Jalankan sync_produk_hpp_periode.py bulan per bulan, "
                    "dari --mulai sampai bulan aktif sekarang."
    )
    parser.add_argument("--mulai", type=str, default="2026-01", help="YYYY-MM bulan awal (default 2026-01)")
    parser.add_argument("--env", action="store_true", help="teruskan ke sync_produk_hpp_periode.py")
    parser.add_argument("--cabang-ids", type=str, default="2,1,4,6,7,5")
    parser.add_argument("--opsi-satuan", type=str, default="default")
    parser.add_argument("--no-create-table", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true",
                        help="lanjut ke bulan berikutnya walau ada bulan yang gagal")
    args = parser.parse_args()

    mulai_y, mulai_m = (int(x) for x in args.mulai.split("-"))
    today = date.today()
    months = []
    y, m = mulai_y, mulai_m
    while (y, m) <= (today.year, today.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    print(f"Periode yang akan diproses ({len(months)} bulan): "
          + ", ".join(f"{y}-{m:02d}" for y, m in months), flush=True)

    failed = []
    for y, m in months:
        awal, akhir = month_range(y, m)
        cmd = [sys.executable, "sync_produk_hpp_periode.py",
               "--tanggal-awal", awal, "--tanggal-akhir", akhir,
               "--cabang-ids", args.cabang_ids, "--opsi-satuan", args.opsi_satuan]
        if args.env:
            cmd.insert(2, "--env")
        if args.no_create_table:
            cmd.append("--no-create-table")
        if args.dry_run:
            cmd.append("--dry-run")
        print(f"\n=== {y}-{m:02d} ({awal} s/d {akhir}) ===", flush=True)
        print("CMD:", " ".join(cmd), flush=True)
        ret = subprocess.run(cmd).returncode
        if ret != 0:
            failed.append((y, m, ret))
            if not args.continue_on_error:
                print(f"Gagal di {y}-{m:02d} (exit {ret}), berhenti. "
                      "Gunakan --continue-on-error untuk lanjut ke bulan berikutnya.", flush=True)
                sys.exit(1)

    if failed:
        print(f"\nSelesai dengan {len(failed)} periode gagal: "
              + ", ".join(f"{y}-{m:02d}" for y, m, _ in failed))
        sys.exit(1)
    print(f"\nSelesai: {len(months)} periode diproses tanpa error.")


if __name__ == "__main__":
    main()
