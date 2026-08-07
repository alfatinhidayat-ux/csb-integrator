"""Ekstrak teks dari det_*.pdf ke /tmp/opencode/*.txt (per-halaman dengan proteksi timeout).

Jalankan dari host:
    .venv/bin/python extract_det.py
"""
import glob
import os
import signal

from pypdf import PdfReader

OUT = "/tmp/opencode"
os.makedirs(OUT, exist_ok=True)


class TimeoutError(Exception):
    pass


def alarm_handler(signum, frame):
    raise TimeoutError()


def extract(f):
    r = PdfReader(f)
    n = len(r.pages)
    out_path = os.path.join(OUT, os.path.basename(f) + ".txt")
    ok = 0
    fail = []
    with open(out_path, "w", encoding="utf-8") as fh:
        for i, page in enumerate(r.pages):
            signal.signal(signal.SIGALRM, alarm_handler)
            signal.alarm(25)
            try:
                fh.write(f"--- page {i + 1} ---\n")
                fh.write(page.extract_text() + "\n")
                ok += 1
            except Exception as e:  # noqa: BLE001
                fh.write(f"--- page {i + 1} --- (ERROR: {e})\n")
                fail.append(i + 1)
            finally:
                signal.alarm(0)
    print(f"{os.path.basename(f):20s} pages={n:3d} ok={ok:3d} fail={fail}")
    return out_path


def main():
    for f in sorted(glob.glob("det_*.pdf")):
        extract(f)
    print("Selesai. Output di", OUT)


if __name__ == "__main__":
    main()
