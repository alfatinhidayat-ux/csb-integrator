# csb-integrator — Agent Guide

Compact instructions for agents working in this repo.

## What this is

Single-file Python app that syncs data from 101 Brighter API endpoints to a MySQL database (`brighter_mirror`). Runs once and exits.

## Entrypoints & architecture

- `main.py` — CLI entrypoint. Parses args, creates `Config`, passes to `SyncRunner`.
- `config.py` — `Config` dataclass. Loads from `.env` via `python-dotenv` or CLI args. `from_env()` reads `BRIGHTER_*` vars.
- `auth.py` — `AuthManager`. `POST /login` with `grant_type=password` gets JWT. Auto-refreshes on 401.
- `db.py` — `DatabaseManager`. PyMySQL with `DictCursor`, `autocommit=False`. Creates tables dynamically from API response shapes. Composite PK `(id, cabang_id)`.
- `endpoints.py` — `ENDPOINTS` list of 101 `Endpoint` dataclass instances. Three strategies: `DELTA`, `FULL_PAGING`, `FULL_REPLACE`. 39 endpoints are `skip=True` (print/static routes). 62 syncable (38 main loop + 24 child endpoints).
- `base.py` — `BaseSyncer`: rate limiting, retries (3), pagination.
- `delta.py` — `DeltaSyncer`: filters by `updated_at`/`created_at` timestamps against `sync_meta.last_synced_at`.
- `full.py` — `FullSyncer`: FULL_PAGING truncates table before upsert; FULL_REPLACE only upserts.
- `runner.py` — `SyncRunner`: orchestrates per-cabang, per-endpoint sync. `clean_start()` truncates all data tables and resets `sync_meta.last_synced_at` to NULL before every run. Child endpoints with `parent_table`/`parent_key` use `ThreadPoolExecutor(max_workers=5)` for concurrent fetch; uses `parent_column` for DB query (falls back to `parent_key`) and `parent_key` for path replacement. Injects both parent key value and `cabang_id` into each child record.

## Critical conventions

- **Every table gets `cabang_id INT NOT NULL` + `synced_at DATETIME`** added automatically.
- **Primary key**: `(id, cabang_id)` composite when API response has `id` field; `id INT AUTO_INCREMENT` otherwise. Every table ALWAYS has a PK.
- **UPSERT** (`ON DUPLICATE KEY UPDATE`) only applies when record has `id` field. Tables without `id` get plain INSERT — duplicates prevented by `clean_start()` truncation.
- **Delta filtering is client-side**: pulls all records with `timestamp_data=true`, then discards those `<= last_synced`.
- **`master_cabang` only syncs for cabang_id=1** (hardcoded in `runner.py:_should_sync`).
- **Endpoints without `cabang_param`** only sync for cabang_id=1.
- **Path param endpoints** (`:id`, `:produk_id`, `:cust_id`) are **skipped** in the main loop — 25 are wired as child endpoints via `parent_table`/`parent_key`/`parent_column`.
- **Child endpoints** auto-inject parent key value and `cabang_id` into each fetched record before upsert. `parent_column` (for DB query) may differ from `parent_key` (for URL path replacement).
- **`clean_start()`** truncates ALL data tables (including child ones) before every run.

## Commands

```powershell
# Docker (primary)
docker compose up

# Manual
pip install -r requirements.txt
python main.py --env

# CLI args
python main.py --username "user" --password "pass" --client-id "id" --client-secret "secret"

# With DB config
python main.py --env --db-host "..." --db-port 3306 --db-user "..." --db-password "..."
```

### Optional flags

| Flag | Default | Effect |
|------|---------|--------|
| `--cabang-ids` | auto-discover | Comma-separated cabang IDs |
| `--results-per-page` | 100 | API page size |
| `--request-delay` | 0.1 | Seconds between requests |
| `-v` / `--verbose` | off | DEBUG-level logging |
| `--child-only` | off | Skip clean_start & main loop, only run child endpoint sync (resume after crash) |

## Sync strategies (3 types)

| Strategy | Behavior | Count |
|----------|----------|-------|
| `DELTA` | Paginate with `timestamp_data=true`, filter by record timestamp, update `sync_meta` | 44 |
| `FULL_PAGING` | Paginate all, truncate table, re-insert | 10 |
| `FULL_REPLACE` | Paginate all, upsert only (no delete) | 39 |
| `skip` | Manually skipped (PDF/static routes) | 39 |

### Child endpoints (25) — parent-child relationships

| Child Table | Parent Table | Parent Key |
|-------------|-------------|------------|
| `sistem_users_cabang` | `sistem_users` | `id` |
| `master_karyawan_gaji` | `master_karyawan` | `id` |
| `master_produk_satuan_konversi` | `master_produk` | `produk_id` |
| `master_produk_satuan_konversi_cabang` | `master_produk` | `produk_id` |
| `master_produk_foto` | `master_produk` | `produk_id` |
| `persediaan_surat_jalan_produk` | `persediaan_surat_jalan` | `id` |
| `persediaan_surat_jalan_file` | `persediaan_surat_jalan` | `id` |
| `persediaan_mutasi_barang_item` | `persediaan_mutasi_barang` | `id` |
| `persediaan_retur_pembelian_item` | `persediaan_retur_pembelian` | `id` |
| `persediaan_penyesuaian_stok_item` | `persediaan_penyesuaian_stok` | `id` |
| `persediaan_barang_titipan_internal_item` | `persediaan_barang_titipan_internal` | `id` |
| `transaksi_order_jual_produk` | `transaksi_order_jual` | `id` |
| `transaksi_surat_kirim_jual_produk` | `transaksi_surat_kirim_jual` | `id` |
| `transaksi_surat_kirim_jual_file` | `transaksi_surat_kirim_jual` | `id` |
| `transaksi_retur_penjualan_produk` | `transaksi_retur_penjualan` | `id` |
| `transaksi_piutang_penjualan_riwayat` | `master_customer` | `cust_id` |
| `transaksi_pelunasan_hutang_item` | `transaksi_pelunasan_hutang` | `id` |
| `transaksi_pelunasan_hutang_foto` | `transaksi_pelunasan_hutang` | `id` |
| `akuntansi_kasbank_masuk_item` | `akuntansi_kasbank_masuk` | `id` |
| `akuntansi_kasbank_masuk_penerimaan_lain` | `akuntansi_kasbank_masuk` | `id` |
| `akuntansi_kasbank_masuk_piutang_karyawan` | `akuntansi_kasbank_masuk` | `id` |
| `akuntansi_kasbank_masuk_bukti_pelunasan` | `akuntansi_kasbank_masuk` | `id` |
| `akuntansi_kasbank_keluar_item` | `akuntansi_kasbank_keluar` | `id` |
| `akuntansi_kasbank_keluar_pengeluaran_lain` | `akuntansi_kasbank_keluar` | `id` |
| `akuntansi_kasbank_keluar_detail_pinjaman_karyawan` | `akuntansi_kasbank_keluar` | `id` |

## Data flow

1. Connect to DB → ensure `sync_meta` table
2. `clean_start()`: truncate all data tables, reset `sync_meta.last_synced_at` to NULL
3. Discover cabangs from API (`/master/cabang?cabang_aktif=Aktif`) or use `--cabang-ids`
4. For each cabang → for each endpoint: sync records → upsert → update `sync_meta`
5. After main loop: run child endpoints concurrently (parent-keyed endpoints like `:produk_id/pfoto`)

## Credentials & .env

- `.env` contains real credentials — **it is gitignored**.
- `.env.example` shows the schema.
- Docker: env vars passed directly in `docker-compose.yml`.
- If `.env` exists, `config.py` auto-loads it via `load_dotenv()` before `Config` is created.

## Dependencies

`httpx`, `pymysql`, `python-dotenv` — no dev dependencies, no test framework, no linting/formatting config.

## What's missing (no CI, no tests)

- Zero test files exist.
- Zero CI/CD workflows.
- No lint/format/typecheck config at all.
- Validate by running: `pip install -r requirements.txt && python main.py --env --verbose --cabang-ids 1`
