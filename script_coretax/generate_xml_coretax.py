#!/usr/bin/env python3
"""
generate_xml_coretax.py
========================
Generate file XML "Faktur Keluaran" untuk diimpor ke Coretax DJP,
diambil dari tabel MySQL `coretax_faktur` & `coretax_detail_faktur`
(hasil sync via sync_coretax.py).

Struktur XML mengikuti:  Sample Faktur PK Template v.1.4.xml
Mapping kolom mengikuti: Sample Faktur PK Template v.1.6.1.xlsx
                         (sheet "Faktur", "DetailFaktur", "Keterangan")

Cara pakai:
    python3 generate_xml_coretax.py
    python3 generate_xml_coretax.py --output namafile.xml
    python3 generate_xml_coretax.py --no-validate   (lewati validasi ketat)

Konfigurasi koneksi DB & NPWP penjual diambil dari file .env
(lihat .env.example)
"""

import os
import sys
import argparse
import logging
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

try:
    import pymysql
except ImportError:  # pymysql baru dibutuhkan saat benar-benar konek ke DB
    pymysql = None

from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

# ============================================================
# KONFIGURASI
# ============================================================
DB_CONFIG = {
    "host": os.getenv("BRIGHTER_DB_HOST", "localhost"),
    "port": int(os.getenv("BRIGHTER_DB_PORT", "3306")),
    "user": os.getenv("BRIGHTER_DB_USER"),
    "password": os.getenv("BRIGHTER_DB_PASSWORD"),
    "database": os.getenv("BRIGHTER_DB_NAME", "brighter_mirror"),
    "charset": "utf8mb4",
}
if pymysql is not None:
    DB_CONFIG["cursorclass"] = pymysql.cursors.DictCursor

TABLE_FAKTUR = os.getenv("TABLE_FAKTUR", "coretax_faktur")
TABLE_DETAIL = os.getenv("TABLE_DETAIL", "coretax_detail_faktur")

# NPWP Penjual (16 digit), dipakai sebagai tag <TIN> di root XML.
# Kalau dikosongkan di .env, akan otomatis diambil dari 16 digit
# pertama kolom "ID TKU Penjual".
SELLER_NPWP_OVERRIDE = os.getenv("SELLER_NPWP", "").strip()

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output_xml")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("coretax_xml")


# ============================================================
# MAPPING KOLOM (sheet "Faktur") -> TAG XML <TaxInvoice>
# Urutan dict = urutan tag pada XML. JANGAN diacak urutannya,
# karena Coretax cukup ketat soal urutan elemen.
# ============================================================
FAKTUR_FIELD_MAP = {
    "Tanggal Faktur": "TaxInvoiceDate",
    "Jenis Faktur": "TaxInvoiceOpt",
    "Kode Transaksi": "TrxCode",
    "Keterangan Tambahan": "AddInfo",
    "Dokumen Pendukung": "CustomDoc",
    "Period Dok Pendukung": "CustomDocMonthYear",
    "Referensi": "RefDesc",
    "Cap Fasilitas": "FacilityStamp",
    "ID TKU Penjual": "SellerIDTKU",
    "NPWP/NIK Pembeli": "BuyerTin",
    "Jenis ID Pembeli": "BuyerDocument",
    "Negara Pembeli": "BuyerCountry",
    "Nomor Dokumen Pembeli": "BuyerDocumentNumber",
    "Nama Pembeli": "BuyerName",
    "Alamat Pembeli": "BuyerAdress",   # ejaan asli template DJP (tanpa 'd' kedua)
    "Email Pembeli": "BuyerEmail",
    "ID TKU Pembeli": "BuyerIDTKU",
}

# ============================================================
# MAPPING KOLOM (sheet "DetailFaktur") -> TAG XML <GoodService>
# ============================================================
DETAIL_FIELD_MAP = {
    "Barang/Jasa": "Opt",
    "Kode Barang Jasa": "Code",
    "Nama Barang/Jasa": "Name",
    "Nama Satuan Ukur": "Unit",
    "Harga Satuan": "Price",
    "Jumlah Barang Jasa": "Qty",
    "Total Diskon": "TotalDiscount",
    "DPP": "TaxBase",
    "DPP Nilai Lain": "OtherTaxBase",
    "Tarif PPN": "VATRate",
    "PPN": "VAT",
    "Tarif PPnBM": "STLGRate",
    "PPnBM": "STLG",
}

# Tag numerik -> diformat maks 2 digit desimal (pembulatan komersial)
NUMERIC_TAGS = {
    "Price", "Qty", "TotalDiscount", "TaxBase", "OtherTaxBase",
    "VATRate", "VAT", "STLGRate", "STLG",
}

# Tag header yang boleh kosong -> jadi <Tag/>
OPTIONAL_HEADER_TAGS = {
    "AddInfo", "CustomDoc", "CustomDocMonthYear", "RefDesc",
    "FacilityStamp", "BuyerEmail",
}

# Kode transaksi yang WAJIB mengisi Keterangan Tambahan & Cap Fasilitas
TRX_CODE_REQUIRES_FACILITY = {"07", "08"}


# ============================================================
# HELPER: normalisasi nama kolom (biar cocok meski beda ejaan
# spasi / underscore / kapital antara Excel & MySQL)
# ============================================================
def normalize(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


class ColumnResolver:
    """Membantu mencari nama kolom asli di hasil query MySQL
    berdasarkan nama kolom versi Excel (yang mungkin beda
    format penamaannya di tabel MySQL)."""

    def __init__(self, sample_row: dict):
        self._lookup = {normalize(k): k for k in sample_row.keys()}

    def get(self, row: dict, excel_col_name: str, default=None):
        key = self._lookup.get(normalize(excel_col_name))
        if key is None:
            return default
        val = row.get(key, default)
        return default if val is None else val

    def has(self, excel_col_name: str) -> bool:
        return normalize(excel_col_name) in self._lookup


# ============================================================
# HELPER: format nilai
# ============================================================
def fmt_date(value) -> str:
    """Konversi tanggal ke format YYYY-MM-DD (sesuai XML)."""
    if value in (None, ""):
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    log.warning("Format tanggal tidak dikenali: %r", value)
    return s


def fmt_number(value) -> str:
    """Format angka: maks 2 digit desimal, bilangan bulat tanpa
    titik desimal (mengikuti contoh resmi: <Price>15000</Price>)."""
    if value in (None, ""):
        return "0"
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        log.warning("Nilai numerik tidak valid: %r -> dianggap 0", value)
        return "0"
    d = d.quantize(Decimal("0.01"))
    if d == d.to_integral_value():
        return str(int(d))
    # buang trailing zero pada desimal (mis. 100.50 -> 100.5)
    s = format(d.normalize(), "f")
    return s


def fmt_text(value, default="") -> str:
    if value is None:
        return default
    return str(value).strip()


# ============================================================
# VALIDASI
# ============================================================
class ValidationIssue(Exception):
    pass


def validate_invoice(header: dict, items: list, baris, strict=True):
    """Validasi dasar sesuai sheet 'Keterangan'. Mengumpulkan
    semua masalah, lalu raise di akhir (kalau strict) atau cuma
    log warning (kalau tidak strict)."""
    problems = []

    seller_idtku = header.get("SellerIDTKU", "")
    if len(seller_idtku) != 22 or not seller_idtku.isdigit():
        problems.append(f"ID TKU Penjual harus 22 digit angka, ditemukan: {seller_idtku!r}")

    buyer_doc_type = header.get("BuyerDocument", "")
    buyer_tin = header.get("BuyerTin", "")
    buyer_idtku = header.get("BuyerIDTKU", "")
    buyer_docnum = header.get("BuyerDocumentNumber", "")

    if buyer_doc_type == "TIN":
        if len(buyer_tin) != 16 or not buyer_tin.isdigit():
            problems.append(f"[Baris {baris}] NPWP Pembeli (TIN) harus 16 digit, ditemukan: {buyer_tin!r}")
        if len(buyer_idtku) != 22 or not buyer_idtku.isdigit():
            problems.append(f"[Baris {baris}] ID TKU Pembeli (TIN) harus 22 digit, ditemukan: {buyer_idtku!r}")
        if buyer_docnum and buyer_docnum != "-":
            problems.append(f"[Baris {baris}] Nomor Dokumen Pembeli untuk Jenis ID TIN seharusnya kosong atau '-', ditemukan: {buyer_docnum!r}")
    else:
        if buyer_tin != "0" * 16:
            problems.append(f"[Baris {baris}] NPWP/NIK Pembeli untuk non-TIN seharusnya 16 digit nol, ditemukan: {buyer_tin!r}")
        if buyer_idtku != "0" * 6:
            problems.append(f"[Baris {baris}] ID TKU Pembeli untuk non-TIN seharusnya '000000', ditemukan: {buyer_idtku!r}")
        if buyer_doc_type != "Other ID" and (not buyer_docnum or buyer_docnum == "-"):
            problems.append(f"[Baris {baris}] Nomor Dokumen Pembeli (NIK/Paspor) wajib diisi untuk Jenis ID {buyer_doc_type!r}")

    trx_code = header.get("TrxCode", "")
    if trx_code in TRX_CODE_REQUIRES_FACILITY:
        if not header.get("AddInfo"):
            problems.append(f"[Baris {baris}] Kode Transaksi {trx_code} wajib mengisi Keterangan Tambahan (AddInfo)")
        if not header.get("FacilityStamp"):
            problems.append(f"[Baris {baris}] Kode Transaksi {trx_code} wajib mengisi Cap Fasilitas (FacilityStamp)")

    if not items:
        problems.append(f"[Baris {baris}] Tidak ada item barang/jasa (DetailFaktur) untuk faktur ini")

    for idx, item in enumerate(items, start=1):
        for key in ("Price", "Qty", "TaxBase", "OtherTaxBase", "VAT"):
            try:
                Decimal(item.get(key, "0"))
            except InvalidOperation:
                problems.append(f"[Baris {baris}, Item {idx}] Nilai {key} tidak valid: {item.get(key)!r}")

    if problems:
        for p in problems:
            log.warning("VALIDASI: %s", p)
        if strict:
            raise ValidationIssue(f"Baris {baris} punya {len(problems)} masalah validasi (lihat log di atas)")


# ============================================================
# AMBIL DATA DARI MYSQL
# ============================================================
def fetch_data():
    if pymysql is None:
        log.error("Library 'pymysql' belum terinstal. Jalankan: pip install pymysql --break-system-packages")
        sys.exit(1)
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM `{TABLE_FAKTUR}`")
            faktur_rows = cur.fetchall()

            cur.execute(f"SELECT * FROM `{TABLE_DETAIL}`")
            detail_rows = cur.fetchall()
    finally:
        conn.close()

    if not faktur_rows:
        log.warning("Tabel %s kosong, tidak ada data untuk diproses.", TABLE_FAKTUR)
        return [], []

    return faktur_rows, detail_rows


# ============================================================
# OLAH DATA: gabungkan header (Faktur) + items (DetailFaktur)
# berdasarkan kolom Baris + batch_id
# ============================================================
def build_invoices(faktur_rows, detail_rows):
    if not faktur_rows:
        return []

    header_resolver = ColumnResolver(faktur_rows[0])
    detail_resolver = ColumnResolver(detail_rows[0]) if detail_rows else None

    # index detail rows by (batch_id, baris)
    detail_index = {}
    if detail_resolver:
        for d in detail_rows:
            baris_val = detail_resolver.get(d, "Baris")
            batch_val = detail_resolver.get(d, "batch_id")
            if baris_val is None or str(baris_val).strip().upper() == "END":
                continue
            key = (batch_val, str(baris_val).strip())
            detail_index.setdefault(key, []).append(d)

    invoices = []
    for f in faktur_rows:
        baris_val = header_resolver.get(f, "Baris")
        batch_val = header_resolver.get(f, "batch_id")
        if baris_val is None:
            continue

        # ---- Header (TaxInvoice) ----
        header = {}
        for excel_col, xml_tag in FAKTUR_FIELD_MAP.items():
            raw = header_resolver.get(f, excel_col)
            if xml_tag == "TaxInvoiceDate":
                header[xml_tag] = fmt_date(raw)
            elif xml_tag == "TaxInvoiceOpt":
                header[xml_tag] = fmt_text(raw, default="Normal") or "Normal"
            elif xml_tag == "TrxCode":
                header[xml_tag] = str(raw).strip().zfill(2) if raw else "04"
            else:
                header[xml_tag] = fmt_text(raw)

        # ---- Normalisasi data pembeli sesuai aturan DJP ----
        buyer_doc = str(header.get("BuyerDocument", "")).strip()
        buyer_tin = str(header.get("BuyerTin", "")).strip()

        # Excel sering memotong leading zero. Jika TIN 15 digit, tambahkan 0 di depan
        if len(buyer_tin) == 15 and buyer_tin.isdigit():
            buyer_tin = "0" + buyer_tin
            header["BuyerTin"] = buyer_tin

        if buyer_doc == "TIN" and len(buyer_tin) == 16 and buyer_tin != "0"*16:
            # NPWP valid
            header["BuyerDocument"] = "TIN"
            header["BuyerDocumentNumber"] = None  # DJP sample uses empty tag
            header["BuyerCountry"] = "IND"
            # Jika NITKU tidak 22 digit, set default 22 digit (NPWP + 6 nol)
            if not header.get("BuyerIDTKU") or len(str(header["BuyerIDTKU"])) != 22:
                header["BuyerIDTKU"] = buyer_tin + "000000"
        elif buyer_doc == "National ID" and len(buyer_tin) == 16:
            # Valid NIK (KTP)
            header["BuyerDocument"] = "National ID"
            header["BuyerDocumentNumber"] = buyer_tin
            header["BuyerTin"] = "0" * 16
            header["BuyerIDTKU"] = "0" * 6
            header["BuyerCountry"] = "IND"
        else:
            # Pelanggan umum / tanpa NPWP/NIK valid:
            header["BuyerDocument"] = "Other ID"
            header["BuyerTin"] = "0" * 16
            header["BuyerIDTKU"] = "0" * 6
            header["BuyerDocumentNumber"] = "-"
            header["BuyerCountry"] = "IND" # DJP sample uses IND

        # ---- Items (GoodService) ----
        items = []
        for d in detail_index.get((batch_val, str(baris_val).strip()), []):
            item = {}
            for excel_col, xml_tag in DETAIL_FIELD_MAP.items():
                raw = detail_resolver.get(d, excel_col)
                if xml_tag in NUMERIC_TAGS:
                    item[xml_tag] = fmt_number(raw)
                elif xml_tag == "Code":
                    item[xml_tag] = str(raw).strip().zfill(6) if raw is not None else "000000"
                else:
                    item[xml_tag] = fmt_text(raw)
            items.append(item)

        invoices.append({"baris": baris_val, "header": header, "items": items})

    return invoices


# ============================================================
# BANGUN XML
# ============================================================
def determine_seller_npwp(invoices):
    if SELLER_NPWP_OVERRIDE:
        return SELLER_NPWP_OVERRIDE
    for inv in invoices:
        idtku = inv["header"].get("SellerIDTKU", "")
        if len(idtku) >= 16:
            return idtku[:16]
    log.error("Tidak bisa menentukan NPWP Penjual (TIN). Set SELLER_NPWP di .env")
    return ""


def build_xml(invoices) -> str:
    root = Element("TaxInvoiceBulk", {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:noNamespaceSchemaLocation": "TaxInvoice.xsd",
    })

    tin = SubElement(root, "TIN")
    tin.text = determine_seller_npwp(invoices)

    list_invoice = SubElement(root, "ListOfTaxInvoice")

    for inv in invoices:
        tax_invoice = SubElement(list_invoice, "TaxInvoice")
        for xml_tag in FAKTUR_FIELD_MAP.values():
            el = SubElement(tax_invoice, xml_tag)
            value = inv["header"].get(xml_tag, "")
            if value:
                el.text = value
            # tag optional dibiarkan kosong -> <Tag/>

        list_goods = SubElement(tax_invoice, "ListOfGoodService")
        for item in inv["items"]:
            gs = SubElement(list_goods, "GoodService")
            for xml_tag in DETAIL_FIELD_MAP.values():
                el = SubElement(gs, xml_tag)
                el.text = item.get(xml_tag, "0" if xml_tag in NUMERIC_TAGS else "")

    rough = tostring(root, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="\t", encoding="utf-8")
    # buang baris kosong yang sering muncul dari minidom
    lines = [ln for ln in pretty.decode("utf-8").splitlines() if ln.strip()]
    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Generate XML Faktur Keluaran Coretax dari MySQL")
    parser.add_argument("-o", "--output", help="Nama file output (default: auto, pakai timestamp)")
    parser.add_argument("--no-validate", action="store_true", help="Lewati validasi ketat (hanya warning)")
    args = parser.parse_args()

    log.info("Mengambil data dari MySQL (%s.%s / %s.%s) ...",
             DB_CONFIG["database"], TABLE_FAKTUR, DB_CONFIG["database"], TABLE_DETAIL)
    faktur_rows, detail_rows = fetch_data()
    if not faktur_rows:
        log.error("Tidak ada data faktur. Keluar.")
        sys.exit(1)

    log.info("Ditemukan %d baris faktur, %d baris detail item.", len(faktur_rows), len(detail_rows))

    invoices = build_invoices(faktur_rows, detail_rows)
    log.info("Berhasil mengelompokkan menjadi %d faktur.", len(invoices))

    strict = not args.no_validate
    total_problems = 0
    for inv in invoices:
        try:
            validate_invoice(inv["header"], inv["items"], inv["baris"], strict=strict)
        except ValidationIssue as e:
            total_problems += 1
            log.error(str(e))

    if strict and total_problems:
        log.error("Ditemukan masalah validasi pada %d faktur. Perbaiki data lalu jalankan ulang,",
                  total_problems)
        log.error("atau jalankan dengan --no-validate untuk tetap generate XML (TIDAK disarankan).")
        sys.exit(1)

    # Pisahkan NPWP (TIN) dan Non-NPWP (Other ID, NIK, dll)
    invoices_npwp = []
    invoices_non_npwp = []
    for inv in invoices:
        if inv["header"].get("BuyerDocument") == "TIN":
            invoices_npwp.append(inv)
        else:
            invoices_non_npwp.append(inv)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    if invoices_npwp:
        xml_content_npwp = build_xml(invoices_npwp)
        filename_npwp = args.output if args.output else f"FakturKeluaran_NPWP_{timestamp}.xml"
        out_path_npwp = os.path.join(OUTPUT_DIR, filename_npwp)
        with open(out_path_npwp, "w", encoding="utf-8") as f:
            f.write(xml_content_npwp)
        log.info("Selesai! File XML (NPWP) tersimpan di: %s", out_path_npwp)
        log.info("Jumlah faktur NPWP: %d | Total item: %d",
                 len(invoices_npwp), sum(len(i["items"]) for i in invoices_npwp))
    
    if invoices_non_npwp:
        xml_content_non_npwp = build_xml(invoices_non_npwp)
        # Jika custom output name diberikan, tambahkan suffix agar tidak tertimpa
        filename_non_npwp = f"{args.output.replace('.xml', '')}_NonNPWP.xml" if args.output else f"FakturKeluaran_NonNPWP_{timestamp}.xml"
        out_path_non_npwp = os.path.join(OUTPUT_DIR, filename_non_npwp)
        with open(out_path_non_npwp, "w", encoding="utf-8") as f:
            f.write(xml_content_non_npwp)
        log.info("Selesai! File XML (Non-NPWP) tersimpan di: %s", out_path_non_npwp)
        log.info("Jumlah faktur Non-NPWP: %d | Total item: %d",
                 len(invoices_non_npwp), sum(len(i["items"]) for i in invoices_non_npwp))


if __name__ == "__main__":
    main()
