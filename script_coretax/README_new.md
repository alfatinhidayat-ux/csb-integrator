# Generator XML Faktur Keluaran - Coretax DJP

Script Python untuk generate file **XML "Faktur Keluaran"** yang siap diimpor ke
Coretax DJP, diambil otomatis dari tabel MySQL hasil `sync_coretax.py`
(`coretax_faktur` & `coretax_detail_faktur`).

Struktur XML & mapping kolom dibangun berdasarkan:
- `Sample Faktur PK Template v.1.4.xml` (contoh struktur XML resmi)
- `Sample Faktur PK Template v.1.6.1.xlsx` (sheet `Faktur`, `DetailFaktur`, `Keterangan`)

---

## 1. Instalasi

```bash
pip install pymysql python-dotenv --break-system-packages
```

## 2. Konfigurasi

Copy `.env.example` jadi `.env`, lalu isi sesuai database kamu (samakan dengan
`.env` yang dipakai `sync_coretax.py`):

```bash
cp .env.example .env
```

Isi penting:
- `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- `SELLER_NPWP` — NPWP penjual 16 digit. Kalau dikosongkan, script otomatis
  ambil dari 16 digit pertama kolom **"ID TKU Penjual"**.

## 3. Menjalankan

```bash
python3 generate_xml_coretax.py
```

Output tersimpan di folder `output_xml/FakturKeluaran_YYYYMMDD_HHMMSS.xml`,
siap di-upload ke menu **e-Faktur > Impor Data** di Coretax.

Opsi tambahan:
```bash
# Tentukan nama file output sendiri
python3 generate_xml_coretax.py -o faktur_juni.xml

# Lewati validasi ketat (TIDAK disarankan, hanya untuk debugging)
python3 generate_xml_coretax.py --no-validate
```

---

## 4. Bagaimana Cara Kerjanya

1. Script mengambil **semua baris** dari tabel `coretax_faktur` dan
   `coretax_detail_faktur` (karena `sync_coretax.py` sudah men-drop &
   mengisi ulang tabel ini setiap kali jalan, isi tabel = data terbaru saja).
2. Data digabung berdasarkan kombinasi **`batch_id` + `Baris`** — satu baris
   di `coretax_faktur` = satu `<TaxInvoice>`, dan setiap baris di
   `coretax_detail_faktur` dengan `Baris` yang sama = satu `<GoodService>`
   di dalamnya.
3. Setiap kolom dipetakan ke tag XML sesuai tabel mapping di bawah.
4. Sebelum ditulis ke XML, data **dinormalisasi**:
   - Tanggal → format `YYYY-MM-DD`
   - Angka → maksimal 2 digit desimal (dibulatkan), bilangan bulat tanpa
     titik desimal
   - **Pembeli ber-NPWP (`Jenis ID Pembeli = TIN`)**: `NPWP/NIK Pembeli` diisi
     16 digit NPWP, `ID TKU Pembeli` diisi 22 digit NITKU, `Nomor Dokumen
     Pembeli` otomatis jadi `-`
   - **Pembeli non-NPWP (NIK/Paspor)**: `NPWP/NIK Pembeli` otomatis diisi
     16 digit nol (`0000000000000000`), `ID TKU Pembeli` otomatis `000000`,
     `Nomor Dokumen Pembeli` diisi nomor NIK/Paspor aslinya
5. Validasi dasar dijalankan untuk setiap faktur (lihat bagian 6). Kalau ada
   masalah, script **berhenti dan tidak generate XML** (kecuali pakai
   `--no-validate`) — supaya kamu tidak upload file yang bakal ditolak Coretax.

---

## 5. Mapping Kolom

### Header faktur (sheet `Faktur` → tag `<TaxInvoice>`)

| Kolom Excel/MySQL        | Tag XML              |
|---------------------------|----------------------|
| Tanggal Faktur            | TaxInvoiceDate       |
| Jenis Faktur               | TaxInvoiceOpt        |
| Kode Transaksi             | TrxCode              |
| Keterangan Tambahan        | AddInfo              |
| Dokumen Pendukung          | CustomDoc            |
| Period Dok Pendukung        | CustomDocMonthYear   |
| Referensi                  | RefDesc              |
| Cap Fasilitas               | FacilityStamp        |
| ID TKU Penjual              | SellerIDTKU          |
| NPWP/NIK Pembeli           | BuyerTin             |
| Jenis ID Pembeli            | BuyerDocument        |
| Negara Pembeli              | BuyerCountry         |
| Nomor Dokumen Pembeli       | BuyerDocumentNumber  |
| Nama Pembeli                | BuyerName            |
| Alamat Pembeli              | BuyerAdress          |
| Email Pembeli               | BuyerEmail           |
| ID TKU Pembeli              | BuyerIDTKU           |

### Item barang/jasa (sheet `DetailFaktur` → tag `<GoodService>`)

| Kolom Excel/MySQL    | Tag XML       |
|------------------------|---------------|
| Barang/Jasa            | Opt           |
| Kode Barang Jasa       | Code          |
| Nama Barang/Jasa       | Name          |
| Nama Satuan Ukur       | Unit          |
| Harga Satuan           | Price         |
| Jumlah Barang Jasa     | Qty           |
| Total Diskon           | TotalDiscount |
| DPP                    | TaxBase       |
| DPP Nilai Lain         | OtherTaxBase  |
| Tarif PPN              | VATRate       |
| PPN                    | VAT           |
| Tarif PPnBM            | STLGRate      |
| PPnBM                  | STLG          |

> Script mencocokkan nama kolom secara fleksibel (mengabaikan
> spasi/kapitalisasi/simbol), jadi kolom MySQL tidak harus persis sama
> ejaannya dengan kolom Excel di atas — yang penting "intinya" sama.
> Kalau kolom MySQL kamu namanya jauh berbeda, edit dict `FAKTUR_FIELD_MAP`
> / `DETAIL_FIELD_MAP` di `generate_xml_coretax.py`.

---

## 6. Validasi yang Dijalankan

Berdasarkan sheet `Keterangan` di template resmi:

- `ID TKU Penjual` harus 22 digit angka
- Jika `Jenis ID Pembeli = TIN`: `NPWP/NIK Pembeli` 16 digit, `ID TKU Pembeli`
  22 digit, `Nomor Dokumen Pembeli = "-"`
- Jika `Jenis ID Pembeli` bukan TIN (NIK/Paspor): `NPWP/NIK Pembeli` harus
  16 digit nol, `ID TKU Pembeli = "000000"`, `Nomor Dokumen Pembeli` wajib
  diisi NIK/Paspor
- Kode Transaksi `07`/`08` wajib mengisi `Keterangan Tambahan` & `Cap
  Fasilitas`
- Setiap faktur harus punya minimal 1 item barang/jasa
- Nilai numerik harus valid (bisa dikonversi ke angka)

---

## 7. File Lain yang Belum Dipakai (untuk referensi masa depan)

Kamu juga upload dua template lain yang **belum** di-cover script ini —
disimpan sebagai catatan kalau nanti dibutuhkan:

- **`Sample_Lampiran_C_Template_v_1_1.xlsx`** — untuk lapor SPT Masa PPN
  sebagai Pemungut PPN Pihak Lain (PMSE/marketplace). Beda kasus dengan
  faktur keluaran biasa.
- **`Sample_Retur_Faktur_PM_Template_v_1_1.xlsx`** — untuk retur/pembatalan
  **Faktur Masukan** (pembelian dari supplier), bukan retur faktur keluaran.

Kalau nanti perlu generator untuk salah satu ini, tinggal bilang — strukturnya
mirip, cuma beda mapping kolom & tag XML-nya.

---

## 8. Troubleshooting

- **"Tidak bisa menentukan NPWP Penjual (TIN)"** → isi `SELLER_NPWP` di `.env`
- **"Ditemukan masalah validasi..."** → baca log warning di atasnya, perbaiki
  data di sumbernya (POS / API), lalu jalankan ulang `sync_coretax.py` dulu
  baru `generate_xml_coretax.py`
- File XML tetap ditolak Coretax meski lolos validasi script ini → cek
  referensi terbaru di sheet `REF-General`, `REF-KetTambahan`,
  `REF-CapFasilitas` pada template Excel (kode transaksi, kode objek, cap
  fasilitas bisa berubah tiap update DJP)



  Saya sudah lihat struktur lengkapnya — XML (`TaxInvoiceBulk` → `TaxInvoice` → `GoodService`) dan template Excel (`Faktur` + `DetailFaktur` + aturan validasi di sheet `Keterangan`). Sudah cukup untuk bikin mapping yang presisi. Saya bangun script-nya sekarang.Sekarang saya test logikanya dengan data contoh (meniru struktur tabel `coretax_faktur` & `coretax_detail_faktur`) untuk memastikan XML yang dihasilkan sesuai format resmi.pymysql tidak tersedia offline di environment ini. Saya buat import-nya lazy (hanya saat dipakai sungguhan), supaya tidak menghalangi testing logika XML-nya, dan supaya error-nya lebih jelas kalau user belum install library.Hasilnya sudah sesuai struktur resmi (cocok dengan `Sample Faktur PK Template v.1.4.xml`). Sekarang saya buat README lengkap dan cek juga 2 file lain yang kamu upload untuk catatan ke depan.Sudah jadi! Sudah saya test dengan data contoh (1 transaksi NPWP/Badan + 1 transaksi non-NPWP/NIK) dan hasil XML-nya cocok dengan struktur `Sample Faktur PK Template v.1.4.xml`.

Ringkasan apa yang dibuat (4 file di folder `coretax_xml_generator`):

1. **`generate_xml_coretax.py`** — script utama. Tarik data dari `coretax_faktur` + `coretax_detail_faktur`, gabung berdasarkan `Baris`+`batch_id`, validasi (NPWP/NITKU, format tanggal, kelengkapan data NPWP vs non-NPWP), lalu tulis file XML siap upload.
2. **`.env.example`** — copy jadi `.env`, isi kredensial MySQL & NPWP penjual.
3. **`README.md`** — dokumentasi lengkap mapping kolom, cara jalan, dan aturan validasi.
4. **`test_generator.py`** — buat ngetes logikanya pakai data dummy tanpa perlu konek MySQL.

Yang perlu kamu lakukan:
1. `pip install pymysql python-dotenv --break-system-packages`
2. `cp .env.example .env`, isi sesuai kredensial DB kamu (sama seperti `sync_coretax.py`)
3. Jalankan `python3 generate_xml_coretax.py`

Satu catatan penting: nama kolom di tabel MySQL kamu mungkin sedikit berbeda penulisannya dari header Excel (misalnya pakai underscore). Script ini sudah saya buat untuk **mencocokkan secara fleksibel** (ignore spasi/kapital/simbol), tapi kalau ternyata MySQL kamu pakai nama kolom yang jauh berbeda, kasih tahu saya struktur kolom aslinya biar saya sesuaikan mapping-nya.
