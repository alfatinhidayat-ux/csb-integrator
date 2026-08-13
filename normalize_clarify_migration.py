"""Normalize data hasil migrasi Brighter di tabel Clarify.

Script ini menyatukan normalisasi yang sudah tersebar di backend/integrator.
Default hanya preview. Gunakan --apply untuk menulis perubahan.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND_DIR = (ROOT / ".." / "csb-backend-api").resolve()
LOG_HANDLE = None


def default_log_path(prefix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "logs" / f"{prefix}_{stamp}.log"


def setup_log(path_value: str | None, prefix: str) -> Path:
    global LOG_HANDLE
    path = Path(path_value) if path_value else default_log_path(prefix)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    LOG_HANDLE = path.open("a", encoding="utf-8", buffering=1)
    return path


def emit(message: str = "") -> None:
    print(message, flush=True)
    if LOG_HANDLE:
        LOG_HANDLE.write(message + "\n")


def php_bin() -> str:
    env = os.environ.get("PHP_BIN")
    if env:
        return env
    laragon = Path(r"D:\laragon\bin\php\php-8.3.26-Win32-vs16-x64\php.exe")
    return str(laragon) if laragon.exists() else "php"


def run(
    title: str,
    cmd: list[str],
    *,
    cwd: Path = ROOT,
    apply: bool,
    index: int | None = None,
    total: int | None = None,
) -> int:
    prefix = f"{index}/{total} " if index is not None and total is not None else ""
    emit(f"\n[{datetime.now():%H:%M:%S}] [{prefix}{title}]")
    emit("  $ " + " ".join(cmd))
    if not apply:
        emit("  status: SKIP eksekusi (dry-run rencana)")
        return 0
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        emit(line.rstrip("\n"))
    rc = proc.wait()
    emit(f"  status: {'OK' if rc == 0 else 'FAIL'} rc={rc}")
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize data migrasi Brighter -> Clarify")
    parser.add_argument("--cabang-ids", default=None)
    parser.add_argument("--tanggal-awal", default="2026-01-01")
    parser.add_argument("--tanggal-akhir", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force-pos", action="store_true")
    parser.add_argument("--skip-pos", action="store_true")
    parser.add_argument("--skip-retur", action="store_true")
    parser.add_argument("--skip-piutang", action="store_true")
    parser.add_argument("--skip-kasbank", action="store_true")
    parser.add_argument("--log-file", default=None, help="File log progres. Default: logs/normalize_*.log")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    log_path = setup_log(args.log_file, "normalize")
    emit("=" * 80)
    emit("NORMALIZE CLARIFY MIGRATION")
    emit(f"Mode   : {'APPLY' if args.apply else 'DRY-RUN'}")
    emit(f"Cabang : {args.cabang_ids or 'semua'}")
    emit(f"Range  : {args.tanggal_awal}..{args.tanggal_akhir or 'default command'}")
    emit(f"Log    : {log_path}")
    emit("=" * 80)

    py = sys.executable
    php = php_bin()
    failures: list[tuple[str, int]] = []
    total_steps = 0
    total_steps += 0 if args.skip_pos else 2
    total_steps += 0 if args.skip_retur else 1
    total_steps += 0 if args.skip_piutang else 2
    total_steps += 0 if args.skip_kasbank else 1
    step_no = 0

    def next_step() -> int:
        nonlocal step_no
        step_no += 1
        return step_no

    if not args.skip_pos:
        cmd = [php, "artisan", "pos:normalize-transactions"]
        if not args.apply:
            cmd.append("--dry-run")
        if args.force_pos:
            cmd.append("--force")
        rc = run("Normalize POS payment/rekening/EDC", cmd, cwd=BACKEND_DIR, apply=True, index=next_step(), total=total_steps)
        if rc:
            failures.append(("normalize pos", rc))

        rc = run(
            "Reckon POS header kasir/customer",
            [php, "artisan", "pos:reckon-headers"],
            cwd=BACKEND_DIR,
            apply=args.apply,
            index=next_step(),
            total=total_steps,
        )
        if rc:
            failures.append(("reckon pos headers", rc))

    if not args.skip_retur:
        rc = run(
            "Normalize/Landing Retur Penjualan",
            [py, "migrate_retur_to_app.py"] + (["--yes"] if args.apply else ["--dry-run"]),
            apply=True,
            index=next_step(),
            total=total_steps,
        )
        if rc:
            failures.append(("retur", rc))

    if not args.skip_piutang:
        rc = run(
            "Normalize Piutang Legacy",
            [php, "artisan", "migrate:legacy-piutang"] + ([] if args.apply else ["--dry-run"]),
            cwd=BACKEND_DIR,
            apply=True,
            index=next_step(),
            total=total_steps,
        )
        if rc:
            failures.append(("piutang", rc))

        rc = run(
            "Normalize Pelunasan Piutang Legacy",
            [php, "artisan", "backfill:piutang-pelunasan"] + ([] if args.apply else ["--dry-run"]),
            cwd=BACKEND_DIR,
            apply=True,
            index=next_step(),
            total=total_steps,
        )
        if rc:
            failures.append(("pelunasan piutang", rc))

    if not args.skip_kasbank:
        # Timestamp script memang idempotent dan hanya mengisi data legacy yang kosong.
        rc = run(
            "Normalize timestamp KasBank legacy",
            [py, "backfill_kasbank_timestamp.py"] + (["--apply"] if args.apply else []),
            apply=True,
            index=next_step(),
            total=total_steps,
        )
        if rc:
            failures.append(("kasbank timestamp", rc))

    emit("\n" + "=" * 80)
    if failures:
        emit("NORMALIZE SELESAI DENGAN PERINGATAN")
        for name, rc in failures:
            emit(f"  {name}: rc={rc}")
        return 1
    emit("NORMALIZE SELESAI OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
