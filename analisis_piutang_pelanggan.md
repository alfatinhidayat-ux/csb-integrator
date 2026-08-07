# Analisis Piutang Pelanggan — Rekon & Relasi ke POS
**Database**: `csb_db`  
**Tabel utama**: `brighter_transaksi_piutang_customer_detail`  
**Diperbarui**: 2026-08-07

---

## Latar Belakang

Data piutang pelanggan diambil dari Brighter API endpoint `/transaksi/piutang_penjualan`
(filter `stat_dok=Tertutup`) oleh `sync_finance.py`. Setiap faktur piutang yang belum
lunas masuk ke tabel `brighter_transaksi_piutang_customer_detail`.

> **Catatan penting**: Filter `min_faktur_tanggal = 2024-01-01` sudah **dihapus** per
> 2026-08-07 agar semua periode faktur masuk dan angka cocok dengan Brighter
> mode "semua periode".

---

## 1. Rekap Total Piutang per Cabang

Angka ini harus cocok dengan laporan **Rekap Piutang Penjualan** di Brighter
(pilih mode "semua periode", tanpa filter tanggal faktur).

```sql
SELECT
    d.cabang_id,
    c.nama,
    COUNT(*)                                                          AS total_faktur,
    COUNT(DISTINCT d.cust)                                            AS total_pelanggan,
    CONCAT('Rp ', FORMAT(COALESCE(SUM(d.total), 0), 0, 'id_ID'))     AS total_piutang,
    CONCAT('Rp ', FORMAT(COALESCE(SUM(d.sisa),  0), 0, 'id_ID'))     AS sisa_piutang,
    SUM(d.cust_data_cust_nama IS NULL OR d.cust_data_cust_nama = '')  AS tanpa_nama
FROM brighter_transaksi_piutang_customer_detail d
JOIN cabang c ON c.id = d.cabang_id
GROUP BY d.cabang_id, c.nama
ORDER BY d.cabang_id;
```

**Hasil terverifikasi (2026-08-07):**

| cabang_id | Nama | Total Faktur | Total Piutang | Sisa Piutang | Tanpa Nama |
|---|---|---|---|---|---|
| 1 | CSB - Kobisonta | 1.161 | Rp 6.423.680.247 | Rp 1.384.689.625 | **0** ✅ |
| 2 | CSB - Bula | 1.300 | Rp 7.389.204.149 | Rp 2.532.607.360 | **0** ✅ |
| 4 | CSB - Mandiri | 429 | Rp 3.162.986.600 | Rp 957.017.500 | **0** ✅ |
| 5 | CSB - Kairatu | 410 | Rp 2.466.238.400 | Rp 574.701.400 | **0** ✅ |
| 7 | CSB - Piru | 188 | Rp 1.144.721.700 | Rp 594.524.500 | **0** ✅ |

> Angka cocok dengan Brighter. Semua pelanggan sudah punya nama.

---

## 2. Rentang Faktur di DB

Memastikan faktur lama (sebelum 2024) sudah masuk setelah filter dihapus.

```sql
SELECT
    cabang_id,
    COUNT(*)            AS total_records,
    MIN(faktur_tanggal) AS faktur_paling_lama,
    MAX(faktur_tanggal) AS faktur_paling_baru
FROM brighter_transaksi_piutang_customer_detail
GROUP BY cabang_id
ORDER BY cabang_id;
```

> Setelah fix, `faktur_paling_lama` cabang 1 = **2021-04-11** (sebelumnya 2024-01-07).

---

## 3. Detail Piutang per Pelanggan (per Cabang)

Untuk dibandingkan baris per baris dengan laporan PDF / tampilan Brighter.

```sql
-- Ganti cabang_id sesuai kebutuhan
SELECT
    d.cabang_id,
    d.cust                  AS cust_id,
    d.cust_data_cust_nama   AS nama_pelanggan,
    d.cust_data_cust_no     AS kode_pelanggan,
    COUNT(*)                AS jml_faktur,
    CONCAT('Rp ', FORMAT(SUM(d.total), 0, 'id_ID')) AS total_piutang,
    CONCAT('Rp ', FORMAT(SUM(d.sisa),  0, 'id_ID')) AS sisa_piutang
FROM brighter_transaksi_piutang_customer_detail d
WHERE d.cabang_id = 1   -- ganti sesuai cabang
GROUP BY d.cabang_id, d.cust, d.cust_data_cust_nama, d.cust_data_cust_no
ORDER BY SUM(d.sisa) DESC;
```

---

## 4. Cek Duplikat Faktur (Deteksi Anomali)

Faktur yang muncul lebih dari 1 baris per cabang bisa mengindikasikan data ganda.

```sql
SELECT
    faktur,
    cabang_id,
    COUNT(*) AS jml
FROM brighter_transaksi_piutang_customer_detail
GROUP BY faktur, cabang_id
HAVING COUNT(*) > 1
ORDER BY jml DESC;
```

---

## 5. Backfill Nama Pelanggan

Beberapa `cust_id` tidak ada di tabel `customer` (csb_db) karena merupakan
pelanggan baru yang belum tersync. Solusi: jalankan script Python berikut yang
mencoba 3 sumber secara berurutan.

**Script**: `backfill_piutang_cust_nama.py`

```powershell
# Preview (tidak ubah DB)
python backfill_piutang_cust_nama.py --env --dry-run -v

# Jalankan sungguhan
python backfill_piutang_cust_nama.py --env

# Untuk cabang tertentu
python backfill_piutang_cust_nama.py --env --cabang-ids 1
```

**Urutan sumber data:**
1. **API Brighter** `/master/customer/:id` — paling akurat
2. **`master_customer`** (csb_db) — fallback local
3. **`brighter_pos.cust_nama`** — fallback dari transaksi POS

**Cek pelanggan yang masih tanpa nama:**
```sql
SELECT
    d.cabang_id,
    d.cust      AS cust_id,
    COUNT(*)    AS jumlah_faktur
FROM brighter_transaksi_piutang_customer_detail d
WHERE d.cust_data_cust_nama IS NULL OR d.cust_data_cust_nama = ''
GROUP BY d.cabang_id, d.cust
ORDER BY d.cabang_id, d.cust;
```

---

## 6. Relasi Piutang ↔ `pos_transactions`

Faktur piutang bisa dihubungkan ke `pos_transactions` melalui chain:

```
brighter_transaksi_piutang_customer_detail.faktur
    = brighter_pos.no_bukti
    → brighter_pos.id = pos_transactions.legacy_id
```

### 6a. Rekap Persentase Link per Cabang

```sql
SELECT
    p.cabang_id,
    c.nama,
    COUNT(*)                                        AS total_faktur_piutang,
    SUM(pt.id IS NOT NULL)                          AS terhubung_pos,
    SUM(pt.id IS NULL)                              AS tidak_terhubung,
    ROUND(SUM(pt.id IS NOT NULL)/COUNT(*)*100, 1)   AS persen_linked
FROM brighter_transaksi_piutang_customer_detail p
JOIN cabang c ON c.id = p.cabang_id
LEFT JOIN brighter_pos bp
    ON bp.no_bukti  = p.faktur
   AND bp.cabang_id = p.cabang_id
LEFT JOIN pos_transactions pt
    ON pt.legacy_id = bp.id
   AND pt.cabang_id = bp.cabang_id
GROUP BY p.cabang_id, c.nama
ORDER BY p.cabang_id;
```

**Hasil:**

| Cabang | Total | Terhubung | Tidak | % |
|---|---|---|---|---|
| Kobisonta | 1.161 | 828 | 333 | 71,3% |
| Bula | 1.300 | 1.059 | 241 | 81,5% |
| Mandiri | 429 | 321 | 108 | 74,8% |
| Kairatu | 410 | 306 | 104 | 74,6% |
| Piru | 188 | 183 | 5 | 97,3% |

### 6b. Analisis Penyebab "Tidak Terhubung"

Yang tidak terhubung terbagi menjadi 2 kategori:

```sql
SELECT
    p.cabang_id,
    c.nama,
    SUM(bp.id IS NULL)                           AS tidak_ada_di_brighter_pos,
    SUM(bp.id IS NOT NULL AND pt.id IS NULL)     AS ada_di_pos_belum_migrasi,
    SUM(pt.id IS NOT NULL)                       AS sudah_linked
FROM brighter_transaksi_piutang_customer_detail p
JOIN cabang c ON c.id = p.cabang_id
LEFT JOIN brighter_pos bp
    ON bp.no_bukti = p.faktur AND bp.cabang_id = p.cabang_id
LEFT JOIN pos_transactions pt
    ON pt.legacy_id = bp.id AND pt.cabang_id = bp.cabang_id
WHERE p.faktur_tanggal >= '2025-01-01'
GROUP BY p.cabang_id, c.nama
ORDER BY p.cabang_id;
```

**Hasil:**

| Cabang | Tidak ada di brighter_pos | Ada, belum migrasi | Linked |
|---|---|---|---|
| Kobisonta | 151 | 5 | 828 |
| Bula | 199 | 5 | 1.059 |
| Mandiri | 91 | 0 | 321 |
| Kairatu | 97 | 7 | 306 |
| Piru | 5 | 0 | 183 |

**Penjelasan:**
- **Tidak ada di `brighter_pos`** → Penjualan kredit via **salesman** (format `PT/`, `WS/`,
  `NI/`, `Wl/`, `PRI/`, `VI/`, `BR/`, dll), bukan transaksi kasir POS.
  **Ini WAJAR dan tidak perlu pos_transactions.**
- **Ada di `brighter_pos` tapi belum migrasi** → **17 transaksi** yang perlu ditindaklanjuti
  (lihat Section 7).

### 6c. Format Faktur: Mana yang Terhubung vs Tidak

```sql
SELECT
    p.cabang_id,
    SUBSTRING_INDEX(p.faktur, '/', 1)   AS prefix_faktur,
    COUNT(*)                            AS jumlah,
    SUM(pt.id IS NOT NULL)              AS linked,
    SUM(pt.id IS NULL)                  AS not_linked
FROM brighter_transaksi_piutang_customer_detail p
LEFT JOIN brighter_pos bp
    ON bp.no_bukti = p.faktur AND bp.cabang_id = p.cabang_id
LEFT JOIN pos_transactions pt
    ON pt.legacy_id = bp.id AND pt.cabang_id = bp.cabang_id
GROUP BY p.cabang_id, SUBSTRING_INDEX(p.faktur, '/', 1)
ORDER BY p.cabang_id, not_linked DESC;
```

> **Pola**: Format `SB/FR/` (kasir) → hampir semua linked.
> Format lain (`SB/PT/`, `SB/WS/`, `CSB/Wl/`, `KRT/PRI/`, dll) → salesman, tidak linked.

---

## 7. Gap Migrasi: `brighter_pos` Belum Masuk `pos_transactions`

### 7a. Semua brighter_pos yang belum migrasi (keseluruhan)

```sql
-- Rekap per cabang: berapa record brighter_pos belum punya pos_transactions
SELECT
    b.cabang_id,
    COUNT(*)                                                                    AS total_brighter_pos,
    SUM(p.legacy_id IS NOT NULL)                                                AS sudah_migrasi,
    SUM(p.legacy_id IS NULL)                                                    AS belum_migrasi,
    ROUND(SUM(p.legacy_id IS NOT NULL) / COUNT(*) * 100, 2)                    AS progres_persen
FROM brighter_pos b
LEFT JOIN (
    SELECT DISTINCT legacy_id, cabang_id
    FROM pos_transactions
    WHERE legacy_id IS NOT NULL
) p ON p.legacy_id = b.id AND p.cabang_id = b.cabang_id
GROUP BY b.cabang_id
ORDER BY b.cabang_id;
```

### 7b. Detail record brighter_pos yang belum migrasi

```sql
-- Semua transaksi di brighter_pos yang BELUM ada di pos_transactions
SELECT
    b.cabang_id,
    b.id            AS brighter_pos_id,
    b.tanggal,
    b.no_bukti,
    b.customer_id,
    b.cust_nama,
    b.status_dokumen,
    b.bayar
FROM brighter_pos b
LEFT JOIN (
    SELECT DISTINCT legacy_id, cabang_id
    FROM pos_transactions
    WHERE legacy_id IS NOT NULL
) p ON p.legacy_id = b.id AND p.cabang_id = b.cabang_id
WHERE p.legacy_id IS NULL
ORDER BY b.cabang_id, b.tanggal DESC;
```

### 7c. Irisan: brighter_pos belum migrasi YANG JUGA punya piutang aktif

Ini yang paling kritis — transaksi yang ada di `brighter_pos`, belum masuk
`pos_transactions`, dan masih punya sisa piutang > 0.

```sql
SELECT
    pi.cabang_id,
    c.nama          AS cabang,
    pi.faktur,
    pi.faktur_tanggal,
    pi.cust_data_cust_nama,
    CONCAT('Rp ', FORMAT(pi.sisa, 0, 'id_ID'))  AS sisa_piutang,
    bp.id           AS brighter_pos_id,
    bp.tanggal      AS pos_tanggal,
    bp.status_dokumen
FROM brighter_transaksi_piutang_customer_detail pi
JOIN cabang c ON c.id = pi.cabang_id
JOIN brighter_pos bp
    ON bp.no_bukti  = pi.faktur
   AND bp.cabang_id = pi.cabang_id
LEFT JOIN pos_transactions pt
    ON pt.legacy_id = bp.id
   AND pt.cabang_id = bp.cabang_id
WHERE pt.id IS NULL
  AND pi.sisa > 0
ORDER BY pi.sisa DESC;
```

**17 transaksi gap migrasi yang ditemukan (2026-08-07):**

| Cabang | Faktur | Pelanggan | Sisa | brighter_pos_id |
|---|---|---|---|---|
| Bula | CSB/Wl/2607-0968 | **ASENG CP** | **Rp 350.000.000** ⚠️ | 155749 |
| Bula | CSB/Wl/2607-0953 | YASSER-KILIGA | Rp 60.355.000 | 155503 |
| Bula | CSB/Wl/2607-0967 | YASSER-KILIGA | Rp 18.170.000 | 155745 |
| Bula | CSB/Wl/2607-0957 | GADJA MADA | Rp 4.380.000 | 155610 |
| Bula | CSB/Wl/2607-0965 | DUTA BANGUNAN | Rp 840.000 | 155647 |
| Kobisonta | SB/WS/2607-1315 | ARDI SB | Rp 600.000 | 155513 |
| Kobisonta | SB/WS/2607-1313 | BU ALFI | Rp 324.000 | 155501 |
| Kairatu | KRT/PRI/2607-0960 | BOSS WILLIAM | Rp 452.000 | 141897 |
| Kairatu | KRT/VI/2607-0077 | BOSS WILLIAM | Rp 105.000 | 141921 |
| Kairatu | KRT/BR/2607-0816 | BOSS WILLIAM | Rp 100.000 | 141134 |
| Kairatu | KRT/PRI/2607-0966 | BOSS WILLIAM | Rp 18.000 | 141903 |
| Kobisonta | SB/NI/2607-0221 | ANI - LOPING | Rp 0 | 155894 |
| Kobisonta | SB/PT/2607-0350 | SANDY SB | Rp 0 | 155780 |
| Kobisonta | SB/NI/2607-0208 | ERIK MERAH PUTIH | Rp 0 | 155459 |
| Bula | CSB/Wl/2607-0957 | GADJA MADA | Rp 0 | 155610 |
| Kairatu | KRT/BR/2607-0844 | TOKO ENGKI | Rp 0 | 141704 |
| Kairatu | KRT/PRI/2607-0900 | TOKO SITI MAKSUM | Rp 0 | 141228 |

> ⚠️ **ASENG CP - Bula** dengan sisa **Rp 350.000.000** adalah prioritas utama
> untuk segera dimigrasi ke `pos_transactions`.

---

## 8. Progress Migrasi brighter_pos Keseluruhan

Query lengkap untuk pantau status migrasi global (dari `progress_migrasi.sql`):

```sql
-- Ringkasan total
SELECT
    (SELECT COUNT(*) FROM brighter_pos)                  AS pos_sumber_total,
    (SELECT COUNT(*) FROM pos_transactions
        WHERE legacy_id IS NOT NULL)                     AS pos_target_migrasi,
    (SELECT COUNT(*) FROM pos_transactions
        WHERE legacy_id IS NULL)                         AS pos_target_real_aplikasi,
    (SELECT COUNT(*) FROM pos_transactions)              AS pos_target_total,
    ROUND(
        (SELECT COUNT(*) FROM pos_transactions WHERE legacy_id IS NOT NULL)
        / (SELECT COUNT(*) FROM brighter_pos) * 100
    , 2)                                                 AS progres_persen;

-- Per cabang
SELECT
    b.cabang_id,
    COUNT(*)                                                                      AS total,
    SUM(p.legacy_id IS NULL)                                                      AS belum_migrasi,
    ROUND(SUM(p.legacy_id IS NOT NULL) / COUNT(*) * 100, 2)                      AS progres_persen
FROM brighter_pos b
LEFT JOIN (
    SELECT DISTINCT legacy_id, cabang_id
    FROM pos_transactions WHERE legacy_id IS NOT NULL
) p ON p.legacy_id = b.id AND p.cabang_id = b.cabang_id
GROUP BY b.cabang_id
ORDER BY b.cabang_id;
```

---

## Referensi File

| File | Keterangan |
|---|---|
| [`sync_finance.py`](file:///d:/CSB%20Project/csb-integrator/sync_finance.py) | Pipeline utama piutang → csb_db |
| [`backfill_piutang_cust_nama.py`](file:///d:/CSB%20Project/csb-integrator/backfill_piutang_cust_nama.py) | Backfill nama pelanggan dari API/master/pos |
| [`endpoints.py`](file:///d:/CSB%20Project/csb-integrator/endpoints.py#L971) | Definisi endpoint Brighter piutang |
| [`progress_migrasi.sql`](file:///d:/CSB%20Project/csb-integrator/progress_migrasi.sql) | Query pantau progress migrasi POS |
| [`sync_pos.py`](file:///d:/CSB%20Project/csb-integrator/sync_pos.py) | Sync brighter_pos dari API |
