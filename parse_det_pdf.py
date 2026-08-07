"""Parse laporan hutang detail (det_*.pdf.txt) menjadi baris per faktur.

Cara kerja: setiap baris faktur diakhiri tepat 3 angka berurutan
(HUTANG AWAL / TERBAYAR / SISA). Run 3+ angka berurutan yang TIDAK
didahului "SUB TOTAL"/"GRAND TOTAL" = baris faktur. NO BUKTI PEMBELIAN
diambil dari prefix /PB|PL/ terakhir sebelum run (karena dalam satu
baris prefix pembelian selalu mendahului prefix hutang /LH/).

Output: det_txt/det_parsed.py (dict cabang -> list of faktur dict)
"""
import re
import os
import glob

BASE = os.path.dirname(os.path.abspath(__file__))
TXT_DIR = os.path.join(BASE, "det_txt")
OUT = os.path.join(TXT_DIR, "det_parsed.py")

CABANG_ID = {"kobisonta": 1, "bula": 2, "mandiri": 4, "kairatu": 5, "piru": 7}

PEMB_PREFIX = re.compile(r"^[A-Z]{2,4}/(PB|PL)/\d{4,6}-?$")
LH_PREFIX = re.compile(r"^[A-Z]{2,4}/LH/\d{4,6}-?$")
NUM = re.compile(r"-?[\d.,]+")


def is_num(s):
    return NUM.fullmatch(s) is not None


def to_int(s):
    return int(s.replace(".", "").replace(",", ""))


def find_amount_runs(lines):
    """Return list of (start_idx, end_idx) for maximal runs of >=3 numerics,
    where line before run is not SUB/GRAND TOTAL."""
    runs = []
    i = 0
    n = len(lines)
    while i < n:
        if is_num(lines[i]):
            j = i
            while j < n and is_num(lines[j]):
                j += 1
            if j - i >= 3:
                prev = lines[i - 1] if i > 0 else ""
                if prev not in ("SUB TOTAL", "GRAND TOTAL"):
                    runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def parse_file(path):
    lines = [l.strip() for l in open(path, encoding="utf-8") if not l.startswith("---")]
    runs = find_amount_runs(lines)
    records = []
    prev_end = 0
    for start, end in runs:
        ha, tb, si = to_int(lines[end - 3]), to_int(lines[end - 2]), to_int(lines[end - 1])
        window = lines[prev_end:start]
        nobukti, supplier = extract_row_info(window, lines, start)
        records.append({"nobukti": nobukti, "supplier": supplier,
                        "hutang_awal": ha, "terbayar": tb, "sisa": si})
        prev_end = start
    return records


def extract_row_info(window, lines, run_start):
    """Dari window (token sebelum run) ambil NO BUKTI PEMBELIAN dan supplier."""
    pemb_idx = None
    for idx in range(len(window) - 1, -1, -1):
        if PEMB_PREFIX.match(window[idx]):
            pemb_idx = idx
            break
    nobukti = None
    if pemb_idx is not None:
        parts = [window[pemb_idx]]
        if pemb_idx + 1 < len(window) and re.fullmatch(r"\d+(-\d+)?", window[pemb_idx + 1]):
            parts.append(window[pemb_idx + 1])
        nobukti = "".join(parts)
    # supplier = token non-numerik sebelum prefix pembelian (lewati header & nomor urut)
    supplier_tokens = []
    for tok in window[:pemb_idx] if pemb_idx is not None else window:
        if re.fullmatch(r"\d+", tok):
            continue
        if tok in ("NO", "NAMA", "SUPPLIER", "NO.", "BUKTI", "PEMBELIAN", "HUTANG",
                   "TANGGAL", "BAYAR", "CABANG", "AWAL", "TERBAYAR", "SISA"):
            continue
        supplier_tokens.append(tok)
    supplier = " ".join(supplier_tokens).strip() or None
    return nobukti, supplier


def main():
    result = {}
    for base, cid in CABANG_ID.items():
        path = os.path.join(TXT_DIR, f"det_{base}.pdf.txt")
        rows = parse_file(path)
        for r in rows:
            r["cabang_id"] = cid
        result[cid] = rows
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("DET = " + repr(result) + "\n")
    print("Parsed:", {cid: len(rows) for cid, rows in result.items()})

    grand = {
        1: (1476320570, 1254345382, 221975188),
        2: (12023195101, 14311681068, 44399387),
        4: (15647762469, 16090077202, 85285266),
        5: (28907619720, 25184719021, 4071096524),
        7: (13339493481, 7559317208, 5853520272),
    }
    print(f'{"c":>2} {"uniq":>6} {"sumHA":>15} {"sumTB":>15} {"sumSI":>15}  pdfHA         pdfTB         pdfSI')
    for cid, rows in result.items():
        agg = {}
        for r in rows:
            k = r["nobukti"]
            if k not in agg:
                agg[k] = {"hutang_awal": r["hutang_awal"], "terbayar": 0}
            agg[k]["terbayar"] += r["terbayar"]
        for v in agg.values():
            v["sisa"] = v["hutang_awal"] - v["terbayar"]
        s = [sum(v[k] for v in agg.values()) for k in ("hutang_awal", "terbayar", "sisa")]
        g = grand[cid]
        ok = all(a == b for a, b in zip(s, g))
        print(f"{cid:>2} {len(agg):>6} {s[0]:>15,} {s[1]:>15,} {s[2]:>15,}  {g[0]:>12,} {g[1]:>12,} {g[2]:>12,}  {'OK' if ok else 'MISMATCH'}")


if __name__ == "__main__":
    main()
