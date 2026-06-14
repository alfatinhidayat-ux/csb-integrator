# Panduan Generator XML Coretax DJP

Modul ini berfungsi untuk menjembatani sistem POS (Koffiesoft) dengan sistem Coretax DJP. Script ini akan secara otomatis mengunduh data Faktur NPWP/KTP dari API, menyimpannya ke MySQL, dan memisahkannya menjadi file-file XML per cabang yang siap diimpor (upload) ke website Coretax.

Terdapat dua script utama yang bekerja secara berurutan:
1. **`sync_coretax.py`**: Bertugas untuk mengunduh Excel dari API untuk semua cabang, mem-parsing isinya, dan memindahkannya ke dalam tabel MySQL (`coretax_faktur` dan `coretax_detail_faktur`).
2. **`script_coretax/generate_xml_coretax.py`**: Bertugas mengambil data dari MySQL, memvalidasi datanya, dan membuat file `.xml` terpisah untuk setiap cabang sesuai dengan standar skema Coretax DJP.

---

## ⚙️ Persyaratan Sistem & Instalasi

Sebelum menjalankan script, pastikan perangkat Anda sudah terinstall **Python 3** dan beberapa *library* pendukung.

1. Buka Terminal / Command Prompt.
2. Jalankan perintah instalasi berikut:
   ```bash
   python3 -m pip install httpx pandas openpyxl pymysql python-dotenv
   ```

## 📝 Konfigurasi Environment (`.env`)

Pastikan Anda memiliki file `.env` di folder utama project ini (sejajar dengan `sync_coretax.py`). File ini harus berisi kredensial database dan konfigurasi URL API.

Contoh isi `.env`:
```env
# Koneksi Database MySQL
BRIGHTER_DB_HOST=127.0.0.1
BRIGHTER_DB_PORT=3306
BRIGHTER_DB_USER=root
BRIGHTER_DB_PASSWORD=password_db_anda
BRIGHTER_DB_NAME=brighter_mirror

# Konfigurasi API
BRIGHTER_BASE_URL=https://brighter-kairatu-api.koffiesoft.com
BRIGHTER_TAX_TOKEN=token_jwt_anda_disini

# URL Target untuk Faktur NPWP/KTP
BRIGHTER_TAX_URL_NPWP=https://brighter-kairatu-api.koffiesoft.com/transaksi/pos/export/coretax?tanggal_awal=2026-05-01&tanggal_akhir=2026-05-31&opsi_ktp_npwp=ktp_npwp&timezone=Asia%2FJakarta

# NPWP Penjual (Opsional, jika kosong akan otomatis mengambil 16 digit pertama ID TKU Penjual)
SELLER_NPWP=0201247236941000
```
> **Catatan:** Pada link `BRIGHTER_TAX_URL_NPWP`, parameter `cabang_id` boleh ada atau tidak, karena script akan otomatis menggantinya (looping) sesuai dengan cabang-cabang yang berstatus "Aktif" di dalam file `cabang.json`.

---

## 🚀 Cara Menjalankan Script

Untuk menghasilkan XML terbaru, Anda harus menjalankan kedua script ini secara berurutan:

### Langkah 1: Tarik Data ke Database (Sync)
Jalankan perintah ini di terminal:
```bash
python3 sync_coretax.py
```
**Apa yang terjadi?**
- Script akan membaca `cabang.json` untuk mengetahui cabang apa saja yang aktif.
- Script me-looping setiap cabang (misal: Cabang 1, 2, 4, 5, 6, 7).
- Untuk tiap cabang, script memanggil endpoint API NPWP, mendownload file Excel-nya sementara ke dalam memori (RAM), lalu mengekstrak sheet `Faktur` dan `DetailFaktur`.
- Semua data lama di tabel database akan dihapus (Refresh), lalu diisi dengan data tarikan terbaru.

### Langkah 2: Buat File XML Coretax (Generate)
Setelah proses Sync selesai, jalankan perintah ini di terminal:
```bash
python3 script_coretax/generate_xml_coretax.py
```
**Apa yang terjadi?**
- Script akan mengambil semua data yang sudah dirapikan di MySQL.
- Melakukan pemformatan angka (maksimal 2 desimal) dan memastikan tanggal sesuai format Coretax (`YYYY-MM-DD`).
- Memisahkan data berdasarkan `cabang_id`.
- Untuk Faktur NPWP/KTP, script akan menulisnya menjadi file `.xml` terpisah per cabang (menggunakan nama asli cabang dari `cabang.json`).

---

## 📂 Lokasi File Hasil

Jika proses berhasil, file XML siap impor akan otomatis tersimpan di dalam folder **`output_xml/`** di folder project Anda.

Format penamaan file hasilnya adalah:
`FakturKeluaran_[NamaCabang]_NPWP_[Timestamp].xml`

Contoh:
- `FakturKeluaran_CSB_Piru_NPWP_20260614_161235.xml`
- `FakturKeluaran_CSB_Kairatu_NPWP_20260614_161235.xml`
- `FakturKeluaran_CSB_Kobisonta_NPWP_20260614_161235.xml`

File-file XML ini dapat langsung Anda unggah (import) ke portal web Coretax DJP.
