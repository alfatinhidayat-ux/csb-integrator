# Panduan Sync Customer ke csb_db

Dokumen ini untuk menjalankan sync khusus API customer ke database `csb_db` (mode `--customer-only`):

- `/master/customer` (upsert per record)

Program menarik **seluruh data customer** dari API (semua halaman sesuai paging, paralel 8 worker), lalu **upsert** (`INSERT ... ON DUPLICATE KEY UPDATE`) ke tabel `customer`. **Hanya tabel `customer` yang disentuh** — tidak ada tabel lain yang di-truncate, di-drop, atau diisi.

## Tabel Target

| API | Tabel | Keterangan |
|-----|-------|------------|
| `/master/customer?timestamp_data=true` | `customer` | Tabel existing aplikasi di `csb_db`. Kolom `cabang_id` ditambahkan otomatis saat run pertama (beserta index). |

Implementasi ada di `customer_sync.py` (class `CustomerSyncer`), dipanggil dari `runner.py` (`run_customer_only`).

### Cara kerja

- Semua halaman `/master/customer` diambil paralel (8 worker) berdasarkan `paging.total_pages`.
- Jumlah record hasil fetch diverifikasi terhadap `paging.total_records`; kalau kurang, otomatis refetch ulang secara sekuensial.
- Field API dipetakan ke kolom tabel: `cust_no`→`kode`, `cust_nama`→`nama`, `cust_hp`→`notelp`, `cust_kelamin`→`kelamin`, `timestamp_data.*`→`created_at/updated_by/...`, dst. Kolom `uuid` diisi UUID deterministik dari `cust_id`.
- `cust_kategori_id` hanya dipakai jika ID-nya ada di tabel `customer_kategori` (ada foreign key); jika tidak, diisi NULL.
- Customer yang muncul di lebih dari satu cabang (mis. `cust_id=1` PELANGGAN UMUM) disimpan sekali saja karena `id` adalah primary key — kemunculan pertama yang dipakai.
- Upsert berbasis PK `id`, jadi data lama yang masih ada di API tetap diperbarui dan record baru ditambahkan.

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
BRIGHTER_BASE_URL=https://brighter-api.koffiesoft.com
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

### Opsi tambahan

| Opsi | Fungsi |
|------|--------|
| `--results-per-page 100` | Jumlah record per halaman API (default 100). |
| `-v` / `--verbose` | Log lebih detail. |

## Output yang Diharapkan

Log sukses akan terlihat seperti ini:

```
  SYNC Brighter Customer            /master/customer
    -> 9375 records fetched
  -> 9375 records upserted into customer
==================================================
SYNC COMPLETE
  Errors:           0
==================================================
```

### Verifikasi cepat di MySQL

```sql
SELECT cabang_id, COUNT(*) FROM csb_db.customer GROUP BY cabang_id;
```

Jumlahnya harus cocok dengan `total_records` di response API (dikurangi customer duplikat lintas cabang yang hanya disimpan sekali).

## Catatan Penting

- Mode ini hanya **upsert** ke tabel `customer` — record yang tidak lagi ada di API tidak dihapus.
- Duplikat `cust_id` antar cabang adalah normal (mis. PELANGGAN UMUM) — muncul sebagai `WARNING` di log dan hanya kemunculan pertama yang disimpan.
- Jika koneksi API terputus di tengah, program retry otomatis (3x per request dengan backoff). Kalau tetap gagal, tabel lama tidak tersentuh — cukup jalankan ulang perintah yang sama.
