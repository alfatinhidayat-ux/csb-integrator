# csb-integrator

Brighter API → MySQL data integrator. Menarik semua data dari 101 endpoint Brighter API ke database MySQL lokal (`brighter_mirror`) secara otomatis.

## Prasyarat

- **Python** 3.10+
- **MySQL** / **MariaDB** (database `brighter_mirror` harus dibuat manual)
- **pip** (Python package manager)

## Setup

```powershell
# 1. Clone / masuk ke folder project
cd csb-integrator

# 2. Install dependencies
pip install -r requirements.txt

# 3. Buat database MySQL
mysql -u root -p -e "CREATE DATABASE brighter_mirror CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

## Konfigurasi

### Opsi A — Environment variables ( `--env` )

Buat file `.env` (jangan di-commit) atau set langsung:

```powershell
$env:BRIGHTER_USERNAME = "username_anda"
$env:BRIGHTER_PASSWORD = "password_anda"
$env:BRIGHTER_CLIENT_ID = "client_id_anda"
$env:BRIGHTER_CLIENT_SECRET = "client_secret_anda"
$env:BRIGHTER_DB_USER = "root"
$env:BRIGHTER_DB_PASSWORD = ""
$env:BRIGHTER_DB_NAME = "brighter_mirror"
```

Jalankan:

```powershell
python main.py --env
```

### Opsi B — CLI arguments

```powershell
python main.py --username "user" --password "pass" --client-id "id" --client-secret "secret"
```

Database default `root@localhost:3306/brighter_mirror` tanpa password. Jika perlu ubah:

```powershell
python main.py --username "user" --password "pass" --client-id "id" --client-secret "secret" `
    --db-host "192.168.1.10" --db-port 3306 --db-user "mirror_user" --db-password "mirror_pass"
```

### Parameter penting

| Parameter | Default | Deskripsi |
|-----------|---------|-----------|
| `--cabang-ids` | auto-detect dari API | Sync cabang tertentu saja, pisah dengan koma |
| `--results-per-page` | 100 | Jumlah record per halaman API |
| `--request-delay` | 0.1 | Jeda antar request (detik) |
| `--verbose` / `-v` | — | Tampilkan log lebih detail |

## Menjalankan

```powershell
python main.py -v --username "user" --password "pass" --client-id "id" --client-secret "secret"
```

Proses akan:

1. Login ke Brighter API → dapat token JWT
2. Deteksi daftar cabang aktif dari API
3. Loop per cabang → loop per endpoint:
   - **DELTA** (44 endpoint): sync perubahan sejak terakhir jalan
   - **FULL_PAGING** (10 endpoint): hapus data lama cabang tsb, tarik ulang semua
   - **FULL_REPLACE** (39 endpoint): upsert data, tidak hapus
   - **Skip** (39 endpoint ⚠️): endpoint dengan path param (`:id`, `:produk_id`, `:cust_id`) — butuh Fase 2
4. Update tabel `sync_meta` (tracking timestamp per endpoint+cabang)
5. Cetak summary

## Struktur tabel

Semua tabel di-*create* otomatis berdasarkan response API pertama. Tiap tabel punya kolom tambahan:

- `cabang_id INT NOT NULL` — milik cabang mana
- `synced_at DATETIME` — kapan terakhir di-sync

Composite primary key: `(id, cabang_id)`.

## Cek hasil

```sql
USE brighter_mirror;
SHOW TABLES;
SELECT COUNT(*) FROM master_cabang;
SELECT * FROM sync_meta;
```

## Troubleshooting

| Error | Solusi |
|-------|--------|
| `Unknown database 'brighter_mirror'` | `CREATE DATABASE brighter_mirror` dulu |
| `Access denied for user` | Cek `--db-user` / `--db-password` |
| `HTTP 401` | Cek username, password, client_id, client_secret |
| `Connection refused` | Pastikan MySQL running di `--db-host`:`--db-port` |
