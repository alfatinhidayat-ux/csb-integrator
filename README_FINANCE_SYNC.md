# Panduan Sync Finance (`sync_finance.py`)

Modul `sync_finance.py` menyinkronkan **master supplier** dan **transaksi keuangan (pelunasan hutang & piutang)** dari API Brighter ke database `csb_db`.

## Perintah

```bash
python sync_finance.py --env                  # semua cabang aktif (auto-discover)
python sync_finance.py --env --cabang-ids 1,6 # cabang tertentu
python sync_finance.py --env --workers 10     # paralel fetch child (default 5)
```

## Rekon pembelian vs pelunasan hutang

Script read-only (`rekon_pembelian_hutang.py`) — membandingkan faktur pembelian vs header/detail pelunasan hutang per cabang, dengan 6 cek konsistensi.

```bash
python rekon_pembelian_hutang.py --env
python rekon_pembelian_hutang.py --env --cabang-ids 1,5
```

Tabel yang dipakai: `brighter_persediaan_pembelian`, `brighter_transaksi_pelunasan_hutang`, `brighter_transaksi_pelunasan_hutang_detail`. Catatan: header pelunasan memakai kolom `stat_dok` (bukan `status_dok`); pembelian memakai `status_dok`.

## Alur kerja (6 langkah per cabang)

| Step | Isi | Endpoint API |
|------|-----|--------------|
| 0 | Master Supplier (upsert per server, ke tabel `supplier`) | `/master/supplier` |
| 1 | Header Pelunasan Hutang | `/transaksi/pelunasan_hutang` |
| 2 | Detail Pelunasan Hutang (concurrent per header) | `/transaksi/pelunasan_hutang/{id}/detail_pelunasan_hutang` |
| 3 | Foto Pelunasan Hutang (concurrent per header) | `/transaksi/pelunasan_hutang/{id}/dfhutang_foto` |
| 4 | Header Pelunasan Piutang | `/transaksi/pelunasan_piutang` |
| 5 | Detail Piutang Customer (concurrent per customer) | `/transaksi/piutang_penjualan/customer/list_piutang/{cust_id}/detail` |

Catatan penting tentang URL: setiap cabang punya server API berbeda (`url_api` di tabel `cabang`). Program login **satu token per server**, dan cabang yang memakai server sama berbagi satu `AuthManager` (tidak spam `/login`).

---

## Tabel yang diisi

| # | Tabel (csb_db) | Jenis | Sumber |
|---|----------------|-------|--------|
| 1 | `supplier` | Master (upsert, **tanpa truncate**) | `/master/supplier` |
| 2 | `brighter_transaksi_pelunasan_hutang` | Header | `/transaksi/pelunasan_hutang` |
| 3 | `brighter_transaksi_pelunasan_hutang_detail` | Detail | `/{id}/detail_pelunasan_hutang` |
| 4 | `brighter_transaksi_pelunasan_hutang_foto` | File | `/{id}/dfhutang_foto` |
| 5 | `brighter_transaksi_pelunasan_piutang` | Header | `/transaksi/pelunasan_piutang` |
| 6 | `brighter_transaksi_piutang_customer_detail` | Detail | `/customer/list_piutang/{cust_id}/detail` |

Tabel pelunasan dibentuk dari respons API yang **di-flatten** (objek bersarang jadi kolom `parent_child`), lalu **prefix field di-strip**. Contoh: `fhutang_id` → `id`, `fhutang_nobukti` → `nobukti`. Tabel dibuat otomatis oleh `ensure_table()` mengikuti bentuk data terbaru, jadi kolom bisa bertambah jika API menambah field.

Setiap tabel data otomatis punya kolom `cabang_id INT NOT NULL` + `synced_at DATETIME`.

**Kunci unik:** `(id, cabang_id)`. Karena respons pelunasan tidak punya kolom `id` dari API, `map_record()` membuat `id` **deterministik** dari isi record (hash MD5). Ini membuat tabel punya PK dan bisa di-upsert berulang tanpa duplikat.

---

## 1. `supplier` (537 baris)

Tabel master yang **sudah ada** di csb_db (bukan dibuat oleh script). Script hanya meng-upsert kolom yang tersedia dari Brighter; kolom CSB lain tidak disentuh. Satu supplier = satu record, `cabang_id` selalu `1`, dan `cabang_id_ref_bright` mencatat server asalnya. Untuk supplier baru dibuatkan akun `authenticated_users` (type `supplier`, password dummy) dan mapping akses cabang di `authenticated_user_cabang`.

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| `id` | bigint PK | = `supplier_id` dari Brighter |
| `uuid` | char | UUID deterministik dari supplier id |
| `authenticated_user_id` | bigint | FK ke `authenticated_users` (1 supplier = 1 user) |
| `kode` | varchar | Kode supplier |
| `nama` | varchar | Nama supplier |
| `aktif` | tinyint | `1` jika `supplier_aktif` = Aktif |
| `cabang_id` | bigint | Selalu `1` (representasi tunggal) |
| `keterangan` | text | Keterangan |
| `provinsi`, `kabupaten`, `kecamatan` | text | Wilayah |
| `alamat` | text | Alamat |
| `notelp` | varchar | Telepon |
| `email` | varchar | Email |
| `npwp` | varchar | NPWP |
| `jatuh_tempo` | int | Jatuh tempo pembayaran (hari) |
| `nama_kontak` | varchar | Nama kontak |
| `notelp_kontak` | varchar | Telepon kontak |
| `created_at` / `updated_at` | timestamp | Waktu buat/ubah |
| `created_by` / `updated_by` | varchar | User pembuat/pengubah |
| `deleted_at` / `deleted_by` | timestamp/varchar | Soft delete |
| `revised` | int | Versi revisi |
| `foto_path` | varchar | Path foto |
| `cabang_id_ref_bright` | int | Cabang referensi tempat supplier ditarik dari Brighter |

---

## 2. `brighter_transaksi_pelunasan_hutang` (611 baris)

Header transaksi **pelunasan hutang ke supplier**. Relasi: `id` → detail & foto.

### Kolom inti (prefix `fhutang_` di API, di-strip)

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| `id` | bigint PK | ID pelunasan hutang (`fhutang_id`) |
| `nobukti` | text | Nomor bukti |
| `supp` | bigint | Supplier yang dilunasi |
| `tanggal` | date | Tanggal transaksi |
| `cara` | text | Cara pembayaran (tunai/transfer, dll) |
| `bayar` | double | Nominal dibayar |
| `keterangan` | text | Keterangan |
| `stat_dok` | text | Status dokumen (Semua/Disetujui, dll) |
| `akun` | bigint | Akun kas/bank |
| `post` | text | Status posting |
| `date_post` | date | Tanggal posting |

### Kolom `timestamp_data_*`
Metadata riwayat dari API (untuk delta/trace): `created_by`, `created_at`, `updated_by`, `updated_at`, `deleted_by`, `deleted_at`, `revised`.

### Kolom `supp_data_*` (snapshot data supplier saat transaksi)
Data supplier yang dibawa API di dalam transaksi: `supplier_id`, `supplier_kode`, `supplier_nama`, `supplier_aktif`, `supplier_keterangan`, `supplier_propinsi_id`, `supplier_kabupaten_kota_id`, `supplier_kecamatan_id`, `supplier_alamat`, `supplier_notelp`, `supplier_email`, `supplier_npwp`, `supplier_jatuh_tempo`.

---

## 3. `brighter_transaksi_pelunasan_hutang_detail` (2.570 baris)

Baris rincian pelunasan hutang (per hutang/PO yang dibayar). Relasi: `fhutang_id` → header.

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| `id` | bigint PK | ID detail (deterministik) |
| `fhutang_id` | bigint | FK ke header pelunasan hutang (di-inject dari parent) |
| `master_lunas_id` | bigint | ID pelunasan induk |
| `nobukti` | text | Nomor bukti |
| `master_hutang_id` | bigint | ID hutang/PO yang dilunasi |
| `hutang_awal` | double | Nilai hutang awal |
| `terbayar` | double | Total sudah terbayar sebelumnya |
| `nilai_bayar` | double | Nilai yang dibayar pada pelunasan ini |
| `sisa_bayar` | double | Sisa hutang setelah bayar |
| `tanggal` | text | Tanggal |
| `keterangan` | text | Keterangan |

### Kolom `master_hutang_data_pembelian_*` (snapshot data pembelian/hutang)
`pembelian_id`, `pembelian_nobukti`, `pembelian_tanggal`, `pembelian_supplier_id`, `pembelian_no_tagihan`, `pembelian_total_qty_produk`, `pembelian_total_biaya_rp`, `pembelian_total_diskon`, `pembelian_total_diskon_rp`, `pembelian_total_net_rp`, `pembelian_total_bayar_rp`, `pembelian_total_sisa_rp`, `pembelian_status_dok`, `pembelian_status_lunas`, `pembelian_keterangan`, `pembelian_request_stat_dok_batal`, `pembelian_request_batal_keterangan`, `pembelian_request_batal_at`, `pembelian_request_batal_by`, `pembelian_approval_batal_at`, `pembelian_approval_batal_by`, `pembelian_cabang_id`.

Plus kolom `timestamp_data_*` dan `synced_at`.

---

## 4. `brighter_transaksi_pelunasan_hutang_foto` (262 baris)

File/foto bukti pelunasan. Relasi: `fhutang_id` → header.

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| `id` | bigint PK | ID foto (deterministik) |
| `fhutang_id` | bigint | FK ke header pelunasan hutang |
| `foto_id` | bigint | ID foto asli dari API |
| `foto_master_id` | bigint | ID master pelunasan |
| `foto_path` | text | Path file |
| `foto_keterangan` | text | Keterangan foto |
| `foto_url` | text | URL file asli |
| `foto_url_medium` / `foto_url_thumbnail` | text | URL ukuran medium/thumbnail |
| `foto_size` / `foto_size_medium` / `foto_size_thumbnail` | bigint | Ukuran file (KB) |

---

## 5. `brighter_transaksi_pelunasan_piutang` (1.464 baris)

Header transaksi **pelunasan piutang customer**. Kolom inti mirip pelunasan hutang:

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| `id` | bigint PK | ID pelunasan piutang (`fpiutang_id`) |
| `nobukti` | text | Nomor bukti |
| `cust` | bigint | Customer yang bayar |
| `tanggal` | date | Tanggal transaksi |
| `cara` | text | Cara pembayaran |
| `bayar` | double | Nominal dibayar |
| `keterangan` | text | Keterangan |
| `stat_dok` | text | Status dokumen |
| `akun` | bigint | Akun kas/bank |
| `post` | text | Status posting |
| `date_post` | date | Tanggal posting |

Plus kolom `timestamp_data_*`.

### Kolom `cust_data_*` (snapshot data customer)
`cust_id`, `cust_cabang_id`, `cust_no`, `cust_kategori_id`, `cust_jns_identitas`, `cust_no_identitas`, `cust_nama`, `cust_kelamin`, `cust_alamat`, `cust_hp`, `cust_email`, `cust_tgllahir`, `cust_keterangan`, `cust_preward_total`, `cust_preward_exp`, `cust_preward_exp_date`, `cust_npwp`, `cust_npwp16`, `cust_nitku`, `cust_aktif`, `cust_deposit_rp`, `cust_foto_data`, `cust_alamat_detail`.

---

## 6. `brighter_transaksi_piutang_customer_detail` (645 baris)

Rincian tagihan piutang per customer (dari `list_piutang/{cust_id}/detail`). Relasi: `fpiutang_cust` → header.

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| `id` | bigint PK | ID detail (deterministik) |
| `fpiutang_cust` | bigint | FK ke customer (`fpiutang_cust` dari header) |
| `faktur` | text | Nomor faktur |
| `faktur_id` | bigint | ID faktur |
| `cust` | bigint | ID customer |
| `faktur_tanggal` | text | Tanggal faktur |
| `keterangan` | text | Keterangan |
| `status` | text | Status piutang |
| `total` | double | Total tagihan |
| `sisa` | double | Sisa belum dibayar |
| `stat_dok` | text | Status dokumen |

Plus kolom `cust_data_*` (sama seperti tabel pelunasan piutang) dan `synced_at`.

---

## Kolom otomatis di semua tabel data

| Kolom | Keterangan |
|-------|------------|
| `cabang_id` | INT NOT NULL — cabang asal data. Selalu di-inject saat sync |
| `synced_at` | DATETIME — waktu record dimasukkan/diupdate ke DB |

## Relasi antar tabel

```
supplier (id)
   ▲
   └─ brighter_transaksi_pelunasan_hutang (supp)     → pelunasan ke supplier
        ├─ brighter_transaksi_pelunasan_hutang_detail (fhutang_id)
        └─ brighter_transaksi_pelunasan_hutang_foto  (fhutang_id)

brighter_transaksi_pelunasan_piutang (cust)
   └─ brighter_transaksi_piutang_customer_detail (fpiutang_cust)
```

## Perilaku khas

- **`supplier`**: upsert-only, tidak pernah di-truncate. `cabang_id=1` untuk semua, akses cabang diatur lewat `authenticated_user_cabang`.
- **Tabel pelunasan**: di-upsert per cabang (truncate per cabang tidak dilakukan script ini; jika ingin snapshot bersih gunakan `main.py`/`--finance-only`). Kolom bertipe tanggal disimpan sebagai `DATE` (tanpa komponen waktu).
- **`id` deterministik**: hash MD5 dari isi record + `cabang_id`, supaya tidak dobel bila dijalankan ulang.
