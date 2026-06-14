"""
test_generator.py
Test build_invoices() & build_xml() pakai data contoh yang
meniru isi tabel coretax_faktur & coretax_detail_faktur
(tanpa perlu koneksi MySQL sungguhan).
"""
import sys
sys.path.insert(0, ".")

from datetime import datetime
import generate_xml_coretax as gen

# --- Contoh isi tabel coretax_faktur (2 baris -> 2 faktur) ---
faktur_rows = [
    {
        "batch_id": "batch-001",
        "kategori_tarikan": "harian",
        "Baris": 1,
        "Tanggal Faktur": datetime(2026, 6, 10),
        "Jenis Faktur": "Normal",
        "Kode Transaksi": "04",
        "Keterangan Tambahan": None,
        "Dokumen Pendukung": None,
        "Period Dok Pendukung": None,
        "Referensi": "INV-0001",
        "Cap Fasilitas": None,
        "ID TKU Penjual": "1090000000002325000000",
        "NPWP/NIK Pembeli": "3174061502560010",
        "Jenis ID Pembeli": "NIK",
        "Negara Pembeli": "IDN",
        "Nomor Dokumen Pembeli": "3174061502560010",
        "Nama Pembeli": "Pelanggan Umum",
        "Alamat Pembeli": "Jl. Mawar No 1, Bandung",
        "Email Pembeli": "pelanggan1@example.com",
        "ID TKU Pembeli": "",
    },
    {
        "batch_id": "batch-001",
        "kategori_tarikan": "harian",
        "Baris": 2,
        "Tanggal Faktur": "10/06/2026",
        "Jenis Faktur": "Normal",
        "Kode Transaksi": "01",
        "Keterangan Tambahan": None,
        "Dokumen Pendukung": None,
        "Period Dok Pendukung": None,
        "Referensi": "INV-0002",
        "Cap Fasilitas": None,
        "ID TKU Penjual": "1090000000002325000000",
        "NPWP/NIK Pembeli": "1090000000002365",
        "Jenis ID Pembeli": "TIN",
        "Negara Pembeli": "IDN",
        "Nomor Dokumen Pembeli": "-",
        "Nama Pembeli": "PT Pelanggan Badan",
        "Alamat Pembeli": "Jl. Asia Afrika No 10, Bandung",
        "Email Pembeli": "finance@pelangganbadan.co.id",
        "ID TKU Pembeli": "1090000000002365000000",
    },
]

# --- Contoh isi tabel coretax_detail_faktur ---
detail_rows = [
    {
        "batch_id": "batch-001",
        "kategori_tarikan": "harian",
        "Baris": 1,
        "Barang/Jasa": "A",
        "Kode Barang Jasa": "000000",
        "Nama Barang/Jasa": "Produk A",
        "Nama Satuan Ukur": "UM.0002",
        "Harga Satuan": 15000,
        "Jumlah Barang Jasa": 200,
        "Total Diskon": 100000,
        "DPP": 2900000,
        "DPP Nilai Lain": 2900000,
        "Tarif PPN": 11,
        "PPN": 319000,
        "Tarif PPnBM": 0,
        "PPnBM": 0,
    },
    {
        "batch_id": "batch-001",
        "kategori_tarikan": "harian",
        "Baris": 2,
        "Barang/Jasa": "B",
        "Kode Barang Jasa": "000000",
        "Nama Barang/Jasa": "Jasa Konsultasi",
        "Nama Satuan Ukur": "UM.0014",
        "Harga Satuan": 5000000.5,
        "Jumlah Barang Jasa": 1,
        "Total Diskon": 0,
        "DPP": 5000000.5,
        "DPP Nilai Lain": 5000000.5,
        "Tarif PPN": 12,
        "PPN": 600000.06,
        "Tarif PPnBM": 0,
        "PPnBM": 0,
    },
]

invoices = gen.build_invoices(faktur_rows, detail_rows)
print(f"Jumlah faktur terbentuk: {len(invoices)}")
for inv in invoices:
    print(f"\n--- Baris {inv['baris']} ---")
    for k, v in inv["header"].items():
        print(f"  {k}: {v!r}")
    for item in inv["items"]:
        print(f"  ITEM: {item}")

print("\n\n=== VALIDASI ===")
for inv in invoices:
    try:
        gen.validate_invoice(inv["header"], inv["items"], inv["baris"], strict=True)
        print(f"Baris {inv['baris']}: OK")
    except gen.ValidationIssue as e:
        print(f"Baris {inv['baris']}: ERROR -> {e}")

print("\n\n=== XML OUTPUT ===")
xml_str = gen.build_xml(invoices)
print(xml_str)
