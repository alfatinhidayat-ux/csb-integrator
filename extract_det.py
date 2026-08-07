"""Ekstrak teks dari det_*.pdf ke det_txt/*.txt (per-halaman, cross-platform).

Jalankan:
    python extract_det.py

Timeout per halaman via multiprocessing (berfungsi juga di Windows,
sebagai pengganti signal.SIGALRM yang hanya ada di POSIX).
"""
import glob
import multiprocessing as mp
import os

from pypdf import PdfReader

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "det_txt")
os.makedirs(OUT, exist_ok=True)

PAGE_TIMEOUT = 25


def _extract_page(pdf_path, page_idx, q):
    try:
        reader = PdfReader(pdf_path)
        q.put((page_idx, reader.pages[page_idx].extract_text() or ""))
    except Exception as e:  # noqa: BLE001
        q.put((page_idx, f"(ERROR: {e})"))


def extract(f):
    ctx = mp.get_context("spawn")
    reader = PdfReader(f)
    n = len(reader.pages)
    del reader
    out_path = os.path.join(OUT, os.path.basename(f) + ".txt")
    ok = 0
    fail = []
    with open(out_path, "w", encoding="utf-8") as fh:
        for page_idx in range(n):
            q = ctx.Queue()
            proc = ctx.Process(target=_extract_page, args=(f, page_idx, q))
            proc.start()
            try:
                _, text = q.get(timeout=PAGE_TIMEOUT)
                fh.write(f"--- page {page_idx + 1} ---\n{text}\n")
                ok += 1
            except Exception:  # noqa: BLE001
                proc.terminate()
                fh.write(f"--- page {page_idx + 1} --- (ERROR: timeout)\n")
                fail.append(page_idx + 1)
            proc.join()
    print(f"{os.path.basename(f):20s} pages={n:3d} ok={ok:3d} fail={fail}")
    return out_path


def main():
    for f in sorted(glob.glob(os.path.join(BASE, "det_*.pdf"))):
        extract(f)
    print("Selesai. Output di", OUT)


if __name__ == "__main__":
    mp.freeze_support()
    main()
