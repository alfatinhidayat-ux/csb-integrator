# Panduan Sync Users ke csb_db

Dokumen ini untuk menjalankan sync khusus dua API berikut ke database `csb_db`:

- `/sistem/users/list`
- `/sistem/users/:id`

Mode ini dibuat agar data users selalu fresh. Setiap kali dijalankan, program akan truncate tabel target terlebih dahulu, lalu menarik ulang data dari API.

## Tabel Target

Data masuk ke database `csb_db` pada tabel:

| API | Tabel |
|-----|-------|
| `/sistem/users/list` | `sistem_users` |
| `/sistem/users/:id` | `sistem_users_detail` |

Selain dua tabel di atas, program juga membuat/mengisi kolom `kode_user` di tabel existing `karyawan`.

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
python main.py --env --users-only
```

Jika ingin melihat log request dan proses lebih detail:

```powershell
python main.py --env --users-only --verbose
```

## Apa yang Dilakukan Program

Saat menjalankan `--users-only`, prosesnya adalah:

1. Login ke Brighter API.
2. Connect ke `csb_db`.
3. Truncate tabel `sistem_users` dan `sistem_users_detail` jika tabel sudah ada.
4. Ambil daftar cabang aktif dari API `/master/cabang`.
5. Untuk setiap cabang, hit `/sistem/users/list` dengan parameter:

```text
page=1
results_per_page=100
user_cabang_id=<cabang_id>
user_aktif=Aktif
data_group_detail=true
user_karyawan_data=true
```

6. Program otomatis lanjut ke page berikutnya jika API mengembalikan `paging.total_pages` lebih dari 1.
7. Isi `user_kode` yang kosong/null dengan kode unik 2 karakter dari `user_name`.
8. Pastikan `user_kode` unik di seluruh tabel `sistem_users`.
9. Simpan hasil list ke `csb_db.sistem_users`.
10. Buat kolom `karyawan.kode_user` jika belum ada.
11. Isi `karyawan.kode_user` dari `sistem_users.user_kode` dengan relasi `sistem_users.user_karyawan = karyawan.id`.
12. Jika satu `karyawan.id` punya lebih dari satu user, program memakai user dengan `user_id` terkecil agar hasilnya konsisten.
13. Ambil semua `user_id` dari `sistem_users`.
14. Untuk setiap `user_id`, hit `/sistem/users/:id`.
15. Simpan hasil detail ke `csb_db.sistem_users_detail`.

## Validasi Setelah Run

Cek jumlah data:

```sql
USE csb_db;

SELECT COUNT(*) FROM sistem_users;
SELECT COUNT(*) FROM sistem_users_detail;
```

Cek jumlah per cabang:

```sql
SELECT cabang_id, COUNT(*) AS total
FROM sistem_users
GROUP BY cabang_id
ORDER BY cabang_id;
```

Cek apakah detail sudah seimbang dengan list:

```sql
SELECT COUNT(DISTINCT user_id) AS total_user_list
FROM sistem_users;

SELECT COUNT(DISTINCT id) AS total_user_detail
FROM sistem_users_detail;
```

Jumlah `total_user_list` dan `total_user_detail` idealnya sama.

Cek hasil relasi ke `karyawan.kode_user`:

```sql
SELECT COUNT(*) AS karyawan_terisi
FROM karyawan
WHERE kode_user IS NOT NULL AND TRIM(kode_user) <> '';

SELECT k.id, k.nama, k.kode_user, su.user_id, su.user_name, su.user_kode
FROM karyawan k
JOIN sistem_users su ON su.user_karyawan = k.id
WHERE k.kode_user IS NOT NULL
ORDER BY k.id
LIMIT 20;
```

Cek apakah semua `user_kode` terisi dan tidak duplikat:

```sql
SELECT COUNT(*) AS kosong
FROM sistem_users
WHERE user_kode IS NULL OR TRIM(user_kode) = '';

SELECT UPPER(TRIM(user_kode)) AS user_kode, COUNT(*) AS total
FROM sistem_users
WHERE user_kode IS NOT NULL AND TRIM(user_kode) <> ''
GROUP BY UPPER(TRIM(user_kode))
HAVING COUNT(*) > 1;
```

## Hasil Run Terakhir

Run terakhir berhasil dengan hasil:

```text
sistem_users: 275 rows
sistem_users_detail: 275 rows
```

Distribusi per cabang:

```text
cabang_id 1: 94
cabang_id 2: 40
cabang_id 4: 34
cabang_id 5: 63
cabang_id 6: 10
cabang_id 7: 34
```

## Catatan Penting

- Mode `--users-only` hanya sync dua API users di atas.
- Data lama pada `sistem_users` dan `sistem_users_detail` akan dihapus dengan `TRUNCATE` sebelum data baru masuk.
- API list user sudah mendukung paging. Jika total data lebih dari 100, program akan otomatis mengambil page 2, 3, dan seterusnya.
- `/sistem/users/:id` memakai `user_id` dari tabel `sistem_users` sebagai nilai pengganti `:id`.

## Troubleshooting

| Masalah | Penyebab Umum | Solusi |
|---------|---------------|--------|
| `Unknown database 'csb_db'` | Database belum dibuat atau `CSB_DB_NAME` salah | Buat database `csb_db` atau perbaiki `.env` |
| `Access denied for user` | User/password DB salah | Cek `CSB_DB_*` atau fallback `BRIGHTER_DB_*` |
| `HTTP 401` | Credential API salah atau token gagal | Cek `BRIGHTER_USERNAME`, `BRIGHTER_PASSWORD`, `BRIGHTER_CLIENT_ID`, `BRIGHTER_CLIENT_SECRET` |
| `table ... doesn't exist` saat validasi | Sync belum berhasil membuat tabel | Jalankan ulang `python main.py --env --users-only --verbose` dan lihat error |
