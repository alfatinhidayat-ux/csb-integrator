"""Parse laporan hutang detail (det_*.pdf.txt) menjadi baris per faktur.

Output: /tmp/opencode/det_parsed.py (dict cabang -> list of faktur dict)
"""
import re
import os

CABANG_NAMES = {"Kobisonta", "Bula", "Mandiri", "Kairatu", "Piru"}
CABANG_ID = {"kobisonta": 1, "bula": 2, "mandiri": 4, "kairatu": 5, "piru": 7}
OUT = "/tmp/opencode/det_parsed.py"


def is_num(s):
    return re.fullmatch(r"-?[\d.,]+", s) is not None


def to_int(s):
    return int(s.replace(".", "").replace(",", ""))


def parse_file(path):
    lines = [l.strip() for l in open(path, encoding="utf-8") if not l.startswith("---")]
    records = []
    pending = []
    i = 0
    n = len(lines)
    while i < n:
        t = lines[i]
        if t == "GRAND TOTAL":
            break
        if t == "SUB TOTAL":
            pending = []
            c = 0
            while c < 3 and i + 1 < n:
                i += 1
                if is_num(lines[i]):
                    c += 1
            i += 1
            continue
        if t in CABANG_NAMES:
            j = i + 1
            vals = []
            while j < n and lines[j] not in CABANG_NAMES and lines[j] != "SUB TOTAL" and lines[j] != "GRAND TOTAL":
                if is_num(lines[j]):
                    vals.append(to_int(lines[j]))
                j += 1
            if len(vals) >= 3:
                ha, tb, si = vals[-3], vals[-2], vals[-1]
                records.append((pending, ha, tb, si))
            pending = []
            i = j
            continue
        pending.append(t)
        i += 1
    return records


def extract_nobukti(seg):
    """Ambil nobukti pembelian dari token seg (awal record)."""
    for idx, tok in enumerate(seg):
        m = re.fullmatch(r"[A-Z0-9]+/[A-Z]{2,3}/\d{4}-", tok)
        if m:
            parts = [tok]
            k = idx + 1
            while k < len(seg) and re.fullmatch(r"[\d]+(-[\d]+)?", seg[k]):
                parts.append(seg[k])
                k += 1
            return "".join(parts)
    return None


def extract_supplier(seg):
    """Nama supplier = token sebelum nobukti pertama."""
    for idx, tok in enumerate(seg):
        if re.fullmatch(r"[A-Z0-9]+/[A-Z]{2,3}/\d{4}-", tok):
            return " ".join(p for p in seg[:idx] if re.fullmatch(r"\d+", p) is None).strip() or None
    return None


def main():
    result = {}
    for base, cid in CABANG_ID.items():
        path = f"/tmp/opencode/det_{base}.pdf.txt"
        records = parse_file(path)
        rows = []
        for seg, ha, tb, si in records:
            rows.append({
                "cabang_id": cid,
                "nobukti": extract_nobukti(seg),
                "supplier": extract_supplier(seg),
                "hutang_awal": ha,
                "terbayar": tb,
                "sisa": si,
            })
        result[cid] = rows
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("DET = " + repr(result) + "\n")
    print("Parsed:", {cid: len(rows) for cid, rows in result.items()})

    # validasi vs grand total recap PDF
    grand = {
        1: (1476320570, 1254345382, 221975188),
        2: (12023195101, 14311681068, 44399387),
        4: (15647762469, 16090077202, 85285266),
        5: (28907619720, 25184719021, 4071096524),
        7: (13339493481, 7559317208, 5853520272),
    }
    print(f'{"c":>2} {"n":>5} {"sumHA":>15} {"sumTB":>15} {"sumSI":>15}  pdfHA      pdfTB      pdfSI')
    for cid, rows in result.items():
        s = [sum(r[k] for r in rows) for k in ("hutang_awal", "terbayar", "sisa")]
        g = grand[cid]
        print(f"{cid:>2} {len(rows):>5} {s[0]:>15,} {s[1]:>15,} {s[2]:>15,}  {g[0]:>12,} {g[1]:>12,} {g[2]:>12,}")


if __name__ == "__main__":
    main()
