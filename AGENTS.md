# brighter-sync

Python data integrator that mirrors Brighter API (legacy) data to a local MySQL database for eventual migration to a new backend.

## Project state

Implemented — Fase 1 (initial load) code complete. No code has been run against a live API yet.

## Architecture

```
brighter-sync/
├── config.py          # Config (env vars / CLI args)
├── auth.py            # OAuth2 password grant, JWT auto-refresh
├── db.py              # MySQL, dynamic table creation, upsert, sync_meta
├── endpoints.py       # Registry of all 101 GET endpoints + sync strategy
├── sync/
│   ├── base.py        # HTTP client, pagination, response parsing, retry
│   ├── delta.py       # Delta sync (timestamp_data + client-side filter)
│   └── full.py        # Full sync (replace or paging-with-upsert)
├── runner.py          # Multi-cabang orchestrator
├── main.py            # CLI entry point
└── requirements.txt
```

## Source API

- **Base URL**: `https://brighter-api.koffiesoft.com`
- **Auth**: `POST /login` with `Content-Type: application/x-www-form-urlencoded`, body `grant_type=password&username=...&password=...&client_id=...&client_secret=...`, returns JWT Bearer token
- **Get List Cabang**: `GET /master/cabang` (open endpoint, no token). Use to discover branch IDs.
- **1 user + 1 token can access all branches** — just pass `cabang_id` as query param

## Endpoint breakdown (verified from code analysis)

| Category | Count | How |
|---|---|---|
| DELTA sync (`timestamp_data=true`) | 44 | Paginate + timestamp → upsert |
| FULL_PAGING (paging, no timestamp) | 10 | Paginate all → delete + insert for cabang |
| FULL_REPLACE (single request) | 39 | Fetch once → upsert |
| SKIP (7 PDF/print + non-GET) | 16 | Not synced |
| **Total** | **109** | |

39 endpoints use path params (`:id`, `:produk_id`, `:cust_id`) — **skipped in Fase 1** since they need parent-ID iteration.

## How to run

```bash
cd brighter-sync
pip install -r requirements.txt
# Via env vars:
$env:BRIGHTER_USERNAME = "user"
$env:BRIGHTER_PASSWORD = "pass"
$env:BRIGHTER_CLIENT_ID = "..."
$env:BRIGHTER_CLIENT_SECRET = "..."
python main.py --env

# Or via CLI:
python main.py --username user --password pass --client-id "" --client-secret ""
```

MySQL must be running with `brighter_mirror` database created. Config defaults to `localhost:3306/brighter_mirror` as `root` (no password). Override with `--db-host`, `--db-user`, `--db-password`, `--db-name`, or `BRIGHTER_DB_*` env vars.

## Key design decisions

| Decision | Choice |
|---|---|
| Target DB | MySQL/MariaDB, new `brighter_mirror` DB |
| Table strategy | Raw mirror — dynamic columns from API response shape, auto-add `cabang_id` + `synced_at` |
| Primary keys | Composite `(id, cabang_id)` when `id` column exists |
| Sync meta | `sync_meta` table tracks `last_synced_at` per endpoint+cabang (resume-safe) |
| Dynamic schema | First API response defines table columns; `ALTER TABLE ADD COLUMN` for new fields |
| Delta strategy | Send `timestamp_data=true`, filter records client-side by `updated_at` / `created_at` |
| Full paging | Paginate all pages, delete cabang's old data, re-insert |
| Full replace | Single fetch, upsert on composite key |

## Known gaps (Fase 2)

- 39 path-param endpoints (`/:id`) need parent-ID resolution before they can sync
- Status-filter variants (Terbuka/Tertutup/Batal) merged into single "Semua" call per endpoint
- Sub-detail endpoints that live under `/:id/...` are skipped entirely
- No continuous scheduling (run manually or wrap in cron/Task Scheduler)
- No data transformation layer (raw mirror only)
