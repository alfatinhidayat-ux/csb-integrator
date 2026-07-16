# Panduan Sync Customer, Deposit, & Pinjaman Karyawan ke csb_db

Dokumen ini untuk menjalankan sync khusus tiga API berikut ke database `csb_db` (mode `--customer-only`):

- `/master/customer` (loop per cabang)
- `/master/deposit`
- `/personalia/pengajuan_pinjaman_karyawan`

Setiap kali dijalankan, program menarik **seluruh data** dari API (semua halaman sesuai paging), lalu **truncate tabel target dan mengisi ulang dengan data baru**. Data lama tidak dihapus sebelum seluruh data API berhasil diambil, jadi kalau API gagal di tengah jalan, isi tabel lama tetap utuh.

## Tabel Target

Data masuk ke database `csb_db` pada tabel:

| API | Tabel | Keterangan |
|-----|-------|------------|
| `/master/customer?timestamp_data=true&cust_cabang_id={cabang_id}` | `customer` | Tabel existing aplikasi. Kolom `cabang_id` ditambahkan otomatis saat run pertama (beserta index). |
| `/master/deposit?timestamp_data=true&deposit_customer_data=true` | `deposit_customer` | Tabel baru, dibuat otomatis. |
| `/personalia/pengajuan_pinjaman_karyawan?timestamp_data=true` | `pengajuan_pinjaman_karyawan` | Tabel baru, dibuat otomatis. |

Implementasi ada di `customer_sync.py` (class `CustomerSyncer`), dipanggil dari `runner.py` (`run_customer_only`).

### Cara kerja per tabel

**1. `customer`**
- Daftar cabang aktif diambil otomatis dari `/master/cabang` (atau pakai `--cabang-ids` untuk membatasi).
- API di-loop per `cabang_id`, semua halaman diambil paralel (8 worker) berdasarkan `paging.total_pages`.
- Jumlah record hasil fetch diverifikasi terhadap `paging.total_records`; kalau kurang, otomatis refetch ulang secara sekuensial.
- Field API dipetakan ke kolom tabel: `cust_no`→`kode`, `cust_nama`→`nama`, `cust_hp`→`notelp`, `cust_kelamin`→`kelamin`, `timestamp_data.*`→`created_at/updated_by/...`, dst. Kolom `uuid` diisi UUID deterministik dari `cust_id`.
- `cust_kategori_id` hanya dipakai jika ID-nya ada di tabel `customer_kategori` (ada foreign key); jika tidak, diisi NULL.
- Customer yang muncul di lebih dari satu cabang (mis. `cust_id=1` PELANGGAN UMUM) disimpan sekali saja karena `id` adalah primary key — kemunculan pertama yang dipakai.
- Truncate memakai `SET FOREIGN_KEY_CHECKS=0` karena tabel `deposit`, `deposit_ledger`, dan `piutang` punya FK ke `customer`.

**2. `deposit_customer`**
- Endpoint deposit mengembalikan data **semua cabang sekaligus** (tiap record punya `deposit_cabang_id`), jadi tidak di-loop per cabang.
- Data customer nested dari API (`deposit_customer_data`) disimpan utuh sebagai kolom JSON.

**3. `pengajuan_pinjaman_karyawan`**
- Sama seperti deposit: endpoint global, tiap record punya `ppinjaman_cabang_id`, tidak perlu loop cabang.
- Semua field `ppinjaman_*` disimpan, `timestamp_data` di-flatten ke kolom `created_by/created_at/updated_by/...`.
- `ppinjaman_karyawan_id` punya **foreign key ke `karyawan(id)`** (`ON DELETE SET NULL`), jadi bisa langsung di-JOIN ke tabel karyawan. Kalau API mengirim `karyawan_id` yang tidak ada di tabel `karyawan`, kolomnya di-set NULL dan dilaporkan sebagai `WARNING` di log (jumlahnya kecil, biasanya karyawan yang belum tersinkron).
- Tabel ini di-**drop dan dibuat ulang** setiap run (bukan sekadar truncate) supaya skemanya selalu mengikuti definisi di kode.

## Konfigurasi Database

Program membaca konfigurasi dari file `.env`.

Untuk koneksi `csb_db`, program memakai variabel berikut jika tersedia:

```ini
CSB_DB_NAME=csb_db
CSB_DB_HOST=...
CSB_DB_PORT=3306
CSB_DB_USER=...
CSB_DB_PASSWORD=...
```

Jika `CSB_DB_HOST`, `CSB_DB_USER`, `CSB_DB_PASSWORD`, atau `CSB_DB_PORT` tidak diisi, program akan fallback memakai koneksi `BRIGHTER_DB_*`, tetapi nama database tetap memakai `CSB_DB_NAME` atau default `csb_db`.

Contoh konfigurasi minimal:

```ini
BRIGHTER_BASE_URL=https://brighter-kairatu-api.koffiesoft.com
BRIGHTER_USERNAME=user
BRIGHTER_PASSWORD=password
BRIGHTER_CLIENT_ID=client_id
BRIGHTER_CLIENT_SECRET=client_secret

BRIGHTER_DB_HOST=31.97.67.49
BRIGHTER_DB_PORT=3306
BRIGHTER_DB_USER=admin
BRIGHTER_DB_PASSWORD=password_db

CSB_DB_NAME=csb_db
```

## Cara Menjalankan

Dari root project:

```powershell
cd "D:\CSB Project\csb-integrator"
python main.py --env --customer-only
```

Satu perintah di atas menjalankan ketiga sync secara berurutan: `customer` → `deposit_customer` → `pengajuan_pinjaman_karyawan`.

### Opsi tambahan

| Opsi | Fungsi |
|------|--------|
| `--cabang-ids 1,2,4` | Batasi sync customer ke cabang tertentu (default: semua cabang aktif dari API). Deposit & pinjaman tetap tarik semua data karena endpoint-nya global. |
| `--results-per-page 100` | Jumlah record per halaman API (default 100). |
| `-v` / `--verbose` | Log lebih detail. |

Contoh:

```powershell
python main.py --env --customer-only --cabang-ids 1,2 -v
```

## Output yang Diharapkan

Log sukses akan terlihat seperti ini:

```
Will sync customer for 6 cabang(s): [7, 6, 5, 4, 2, 1]
  SYNC Brighter Customer            /master/customer cabang=7
  Cabang 7: 90 records / 1 pages
  ...
  -> 9375 records inserted into customer (old data truncated)
  SYNC Brighter Deposit Customer    /master/deposit
  Deposit: 11 records / 1 pages
  -> 11 records inserted into deposit_customer (old data truncated)
  SYNC Brighter Pengajuan Pinjaman Karyawan  /personalia/pengajuan_pinjaman_karyawan
  Pinjaman Karyawan: 438 records / 5 pages
  -> 438 records inserted into pengajuan_pinjaman_karyawan (old data truncated)
==================================================
SYNC COMPLETE
  Errors:           0
==================================================
```

Pastikan `Errors: 0` di ringkasan akhir.

### Verifikasi cepat di MySQL

```sql
SELECT cabang_id, COUNT(*) FROM csb_db.customer GROUP BY cabang_id;
SELECT COUNT(*) FROM csb_db.deposit_customer;
SELECT ppinjaman_cabang_id, COUNT(*) FROM csb_db.pengajuan_pinjaman_karyawan GROUP BY ppinjaman_cabang_id;
```

Jumlahnya harus cocok dengan `total_records` di response API (dikurangi customer duplikat lintas cabang yang hanya disimpan sekali).

## Catatan Penting

- Mode ini **menghapus semua isi** tabel `customer`, `deposit_customer`, dan `pengajuan_pinjaman_karyawan` lalu mengisi ulang. Jangan jalankan kalau ada data manual di tabel-tabel itu yang belum dibackup.
- Duplikat `cust_id` antar cabang adalah normal (mis. PELANGGAN UMUM) — muncul sebagai `WARNING` di log dan hanya kemunculan pertama yang disimpan.
- Jika koneksi API terputus di tengah, program retry otomatis (3x per request dengan backoff). Kalau tetap gagal, tabel lama tidak tersentuh — cukup jalankan ulang perintah yang sama.
