# Panduan Sinkronisasi POS (Header & Detail) ke csb_db

Modul `sync_pos.py` digunakan untuk menyinkronkan data Transaksi POS (Penjualan Langsung) dari Brighter API ke dalam database MySQL (`csb_db`). Modul ini mengunduh data header transaksi sekaligus detail barang/produk yang dibeli secara bersamaan (concurrently) menggunakan Thread Pool.

---

## Cara Menjalankan Perintah

Perintah dasar untuk menjalankan sinkronisasi:

```powershell
python sync_pos.py --env --cabang-ids 1 --tanggal-awal 2026-07-25 --tanggal-akhir 2026-07-30
```

### Parameter CLI (Command Line Interface)

| Parameter | Fungsi | Keterangan |
| :--- | :--- | :--- |
| `-e` / `--env` | Memuat kredensial dari file `.env`. | Selalu gunakan flag ini untuk menggunakan konfigurasi otomatis dari file `.env`. |
| `--cabang-ids` | Memilih ID cabang spesifik (koma dipisah). | Contoh: `--cabang-ids 1,2,6`. Jika dikosongkan, script otomatis mencari cabang yang aktif dari database. |
| `--tanggal-awal` | Menyaring tanggal awal transaksi. | Format: `YYYY-MM-DD`. Contoh: `2026-07-25`. |
| `--tanggal-akhir` | Menyaring tanggal akhir transaksi. | Format: `YYYY-MM-DD`. Contoh: `2026-07-30`. |
| `--all-history` | Menarik seluruh sejarah transaksi POS. | **Perhatian**: Karena data POS sangat banyak (114.000+ data), menarik data sejarah penuh akan memakan waktu dan kuota request API yang besar. |
| `--verbose` | Mengaktifkan logging debug secara detail. | Berguna jika terjadi error koneksi atau mapping data untuk melihat trace log. |

> **Catatan Penting**:
> Jika `--tanggal-awal`, `--tanggal-akhir`, dan `--all-history` tidak ditentukan, script akan otomatis menyaring data dari **1 Januari 2026 s.d. hari ini**.

---

## Cara Kerja Alur Program (Data Flow)

1. **Rebuild Table**: Script akan membuat tabel `brighter_pos` dan `brighter_pos_detail` jika belum ada (`CREATE TABLE IF NOT EXISTS` — tidak menghapus data yang sudah ada).
2. **Discover Cabang**: Mencari daftar cabang aktif untuk dijalankan satu per satu.
3. **Fetch Header**: Mengambil data header transaksi dari API `/transaksi/pos` per halaman (100 data per request).
   * **Penting**: API tidak mendukung filter tanggal server-side dan **tidak mengurutkan data berdasarkan tanggal**. Urutan API berdasarkan `id`, sementara `jproduk_tanggal` bisa diubah terpisah sehingga tidak selalu berurutan dengan `id`. Oleh karena itu script **menelusuri seluruh halaman** dan memfilter tanggal di sisi klien. Optimasi "early stopping" yang lama dihapus karena menyebabkan banyak tanggal terlewat (tidak tersinkron).
4. **Fetch Details (Concurrent)**: Menggunakan `ThreadPoolExecutor` (3 worker thread secara paralel) untuk menembak detail item barang yang dibeli per transaksi melalui `/transaksi/pos/:id/detail_pos`.
5. **Flattening & Ingestion**: Seluruh nested JSON (seperti detail data customer, timestamp, cara bayar, dan satuan barang) dipecah menjadi kolom-kolom biasa di MySQL, kemudian dimasukkan secara massal (`batch insert`) menggunakan perintah `executemany` MySQL untuk kinerja maksimal.

---

## Penjelasan Fungsi di [sync_pos.py](file:///d:/CSB%20Project/csb-integrator/sync_pos.py)

### 1. `ensure_tables(db)`
* **Kegunaan**: Memastikan tabel `brighter_pos` dan `brighter_pos_detail` sudah ada sebelum sinkronisasi dimulai.
* **Proses**: Membuat tabel jika belum ada (`CREATE TABLE IF NOT EXISTS`) dengan primary key `(id, cabang_id)` dan tipe data kolom terstandardisasi (seperti `Decimal` untuk mata uang dan `DATETIME` untuk waktu). **Tidak menghapus** tabel/data yang sudah ada — sinkronisasi ulang bersifat upsert (idempoten).

### 2. `map_header(rec, cabang_id)`
* **Kegunaan**: Melakukan pemetaan (*mapping*) data header transaksi dari response JSON API ke kolom tabel `brighter_pos`.
* **Proses**: 
  * Menghilangkan prefix `jproduk_` pada nama variabel API (misal: `jproduk_id` diubah menjadi `id`, `jproduk_tanggal` menjadi `tanggal`).
  * Memecah (*flatten*) dictionary `timestamp_data` ke dalam kolom `created_by`, `created_at`, dll.
  * Memecah (*flatten*) dictionary `jproduk_cust_data` ke kolom `cust_nama`, `cust_no`, dll.
  * Membaca array `jproduk_cara_bayar_data` untuk mengambil metode pembayaran pertama ke kolom `cbayar_nama`, `cbayar_nilai_bayar_rp`, dll. Serta menggabungkan seluruh metode pembayaran alternatif ke dalam satu kolom string `cbayar_all_methods`.

### 3. `map_detail(rec, cabang_id)`
* **Kegunaan**: Melakukan pemetaan (*mapping*) item barang dari response JSON API detail produk ke kolom tabel `brighter_pos_detail`.
* **Proses**:
  * Menghilangkan prefix `dproduk_` pada nama variabel API (misal: `dproduk_id` menjadi `id`, `dproduk_master` menjadi `pos_id`).
  * Mengubah `dproduk_produk` menjadi `produk_id` (foreign key ke tabel master produk).
  * Memecah (*flatten*) dictionary `dproduk_produk_data` ke kolom `produk_nama`, `produk_kode`, `produk_sku`, dll.
  * Memecah (*flatten*) dictionary `dproduk_satuan_data` ke kolom `satuan_code` dan `satuan_nama`.

### 4. `fetch_pos_headers(config, auth, cabang_id, tanggal_awal, tanggal_akhir)`
* **Kegunaan**: Menarik data transaksi header secara halaman demi halaman (pagination) dari API `/transaksi/pos`.
* **Fitur Utama**: Menelusuri **seluruh halaman** (berdasarkan `paging.total_pages`) lalu memfilter tanggal secara klien-side. Tidak ada "early stopping" karena API tidak menjamin urutan tanggal. Dilengkapi deduplikasi per `id` untuk mengantisipasi record ganda bila ada data baru yang masuk saat proses berjalan.

### 5. `fetch_pos_detail(config, auth, pos_id)`
* **Kegunaan**: Memanggil API `/transaksi/pos/:id/detail_pos` dengan parameter tambahan `dproduk_produk_data=true` dan `dproduk_satuan_data=true` untuk mendapatkan informasi barang secara lengkap.

### 6. `insert_batch(db, table, records)`
* **Kegunaan**: Menyisipkan baris data dalam jumlah besar ke database secara efisien.
* **Proses**: Menyusun query dinamis `INSERT INTO table (columns) VALUES (%s)` dan mengeksekusinya dalam satu batch via `cursor.executemany()` untuk meminimalkan beban I/O pada MySQL server.
