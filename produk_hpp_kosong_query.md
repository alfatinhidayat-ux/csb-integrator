# Produk HPP Kosong / Belum Terisi — Query Pengisian (Fokus Produk Terjual)

Dibuat: 10 Agu 2026. Berlaku untuk tabel `produk_hpp_periode`
(sumber HPP dashboard, diisi script `sync_produk_hpp_periode.py`
dengan prioritas `hpp_sistem` → `pembelian` → `harga_beli_terakhir`).

## Prinsip Pengisian

- **Produk tampil di semua cabang** (tidak ada pengaturan per cabang), maka
  **HPP diisi identik per produk** untuk semua cabang — cukup cari nilai HPP
  per satuan sekali, lalu `UPDATE` semua baris produk tsb.
- **Fokus ke produk yang benar-benar terjual** (ada di `pos_transaction_items`)
  pada periode **Januari–Agustus 2026**. Produk tanpa penjualan tidak berdampak
  ke HPP dashboard, tidak perlu dikejar.

## Ringkasan Temuan (Jan–Agu 2026)

| Uraian | Jumlah |
|---|---|
| Baris kosong di `produk_hpp_periode` (semua produk, semua periode) | 48.873 |
| Produk unik HPP kosong (per bulan) | ~1.013–1.039 |
| **Produk unik HPP kosong YANG TERJUAL (Jan–Agu)** | **128** |
| Baris kosong milik 128 produk terjual | 5.039 |
| **Produk TIDAK ada di `produk_hpp_periode` tapi terjual (Jan–Agu)** | **46** |

Dengan fokus ini, pekerjaan menyusut dari ~1.033 produk menjadi
**128 + 46 = 174 produk** (≈ 83% lebih sedikit).

Catatan untuk 46 produk yang tidak ada di tabel: selama tidak ada baris
`produk_hpp_periode`, HPP penjualannya otomatis 0 (LEFT JOIN kosong). Untuk
mengisi, barisnya perlu di-`INSERT` (lihat Cara Mengisi bagian bawah).

Rincian per periode — produk unik HPP kosong yang terjual di periode tsb:

| Periode | Produk unik terjual (HPP kosong) |
|---|---|
| 2026-01 | 21 |
| 2026-02 | 18 |
| 2026-03 | 23 |
| 2026-04 | 24 |
| 2026-05 | 27 |
| 2026-06 | 43 |
| 2026-07 | 39 |
| 2026-08 | 28 |

---

## Satuan HPP

**Satuan HPP = kolom `produk_hpp_periode.satuan_kode`** (bukan `satuan_id`,
yang memakai ruang id satuan Brighter). Konsisten dengan tabel `satuan`:

| satuan_kode | satuan.nama |
|---|---|
| PCS | PIECES |
| SET | SET |
| KTN | KTN |
| MTR | METER |
| STF | STAF |
| PAK | PAK |
| KG | KILOGRAM |
| ROL | ROL |
| LSN | LUSIN |
| PKT | PAKET |
| DOS | DOS |
| LTR | LITER |
| SAK | SAK |
| DRUM | DRUM |

Contoh tampilan satuan + nama:

```sql
SELECT DISTINCT h.satuan_kode, s.nama
FROM produk_hpp_periode h
LEFT JOIN satuan s ON s.kode = h.satuan_kode
WHERE h.periode_awal BETWEEN '2026-01-01' AND '2026-08-31'
ORDER BY h.satuan_kode;
```

---

## Query 1 — Daftar 128 Produk HPP Kosong Yang Terjual (FOKUS, Jan–Agu 2026)

Dedupe per produk, beserta satuan, total qty terjual, periode, dan cabang.

```sql
SELECT
    h.produk_kode,
    h.produk_nama,
    h.satuan_kode,
    s.nama AS satuan_nama,
    ROUND(SUM(i.quantity * i.unit_factor), 2) AS total_qty_terjual,
    COUNT(DISTINCT DATE_FORMAT(t.waktu_transaksi, '%Y-%m')) AS jml_periode,
    GROUP_CONCAT(DISTINCT DATE_FORMAT(t.waktu_transaksi, '%m')
                 ORDER BY DATE_FORMAT(t.waktu_transaksi, '%m')) AS periode,
    COUNT(DISTINCT t.cabang_id) AS jml_cabang,
    GROUP_CONCAT(DISTINCT c.kode ORDER BY c.kode) AS cabang_terjual,
    GROUP_CONCAT(DISTINCT h.sumber_hpp ORDER BY h.sumber_hpp) AS sumber_hpp
FROM produk_hpp_periode h
JOIN pos_transaction_items i ON i.product_id = h.produk_id
JOIN pos_transactions t      ON t.id = i.pos_transaction_id
JOIN cabang c                ON c.id = t.cabang_id
LEFT JOIN satuan s           ON s.kode = h.satuan_kode
WHERE h.periode_awal BETWEEN '2026-01-01' AND '2026-08-31'
  AND (h.hpp_moving_average IS NULL OR h.hpp_moving_average = 0)
  AND DATE_FORMAT(t.waktu_transaksi, '%Y-%m-01') = h.periode_awal
GROUP BY h.produk_id, h.produk_kode, h.produk_nama, h.satuan_kode, s.nama
ORDER BY total_qty_terjual DESC;
```

### Variasi — dengan referensi harga beli (untuk panduan mengisi)

```sql
SELECT
    h.produk_kode,
    h.produk_nama,
    h.satuan_kode,
    s.nama AS satuan_nama,
    ROUND(SUM(i.quantity * i.unit_factor), 2) AS total_qty_terjual,
    p.produk_harga_beli_terakhir AS ref_harga_beli_terakhir,
    p.produk_satuan_beli_terakhir AS ref_satuan_beli,
    bd.pembelian_det_produk_harga AS ref_harga_beli_riwayat,
    GROUP_CONCAT(DISTINCT h.sumber_hpp ORDER BY h.sumber_hpp) AS sumber_hpp
FROM produk_hpp_periode h
JOIN pos_transaction_items i ON i.product_id = h.produk_id
JOIN pos_transactions t      ON t.id = i.pos_transaction_id
LEFT JOIN satuan s           ON s.kode = h.satuan_kode
LEFT JOIN produk p           ON p.produk_id = h.produk_id
LEFT JOIN (
    SELECT pembelian_det_produk_id, cabang_id,
           MAX(pembelian_det_produk_harga) AS pembelian_det_produk_harga
    FROM brighter_persediaan_pembelian_detail
    GROUP BY pembelian_det_produk_id, cabang_id
) bd ON bd.pembelian_det_produk_id = h.produk_id
    AND bd.cabang_id = h.cabang_id
WHERE h.periode_awal BETWEEN '2026-01-01' AND '2026-08-31'
  AND (h.hpp_moving_average IS NULL OR h.hpp_moving_average = 0)
  AND DATE_FORMAT(t.waktu_transaksi, '%Y-%m-01') = h.periode_awal
GROUP BY h.produk_id, h.produk_kode, h.produk_nama, h.satuan_kode, s.nama,
         p.produk_harga_beli_terakhir, p.produk_satuan_beli_terakhir,
         bd.pembelian_det_produk_harga
ORDER BY total_qty_terjual DESC;
```

> Catatan: untuk 128 produk ini, `produk.harga_beli_terakhir` seluruhnya 0 dan
> tidak ada `produk_cost_histories`. Sebagian kecil (15 produk) punya riwayat
> `brighter_persediaan_pembelian_detail` — nilai `pembelian_det_produk_harga`
> bisa dipakai sebagai referensi. Sisanya harus dicari dari invoice/supplier.

---

## Query 2 — Produk yang TIDAK ADA di `produk_hpp_periode` tapi Terjual

Produk di tabel `produk` yang tidak pernah masuk `produk_hpp_periode` sama
sekali, dan benar-benar terjual pada rentang periode Jan–Agu 2026
(**46 produk**).

```sql
SELECT
    p.produk_kode,
    p.produk_nama,
    COALESCE(p.produk_satuan_default, p.produk_satuan) AS satuan_id_produk,
    s.nama AS satuan_nama,
    ROUND(SUM(i.quantity * i.unit_factor), 2) AS qty_terjual,
    COUNT(DISTINCT DATE_FORMAT(t.waktu_transaksi, '%Y-%m')) AS jml_periode,
    GROUP_CONCAT(DISTINCT DATE_FORMAT(t.waktu_transaksi, '%m')
                 ORDER BY DATE_FORMAT(t.waktu_transaksi, '%m')) AS periode,
    GROUP_CONCAT(DISTINCT c.kode ORDER BY c.kode) AS cabang_terjual,
    p.produk_harga_beli_terakhir AS ref_harga_beli_terakhir,
    bd.pembelian_det_produk_harga AS ref_harga_beli_riwayat
FROM pos_transaction_items i
JOIN pos_transactions t ON t.id = i.pos_transaction_id
JOIN produk p           ON p.produk_id = i.product_id
LEFT JOIN satuan s      ON s.id = p.produk_satuan_default
LEFT JOIN (
    SELECT pembelian_det_produk_id, cabang_id,
           MAX(pembelian_det_produk_harga) AS pembelian_det_produk_harga
    FROM brighter_persediaan_pembelian_detail
    GROUP BY pembelian_det_produk_id, cabang_id
) bd ON bd.pembelian_det_produk_id = p.produk_id
    AND bd.cabang_id = t.cabang_id
WHERE DATE_FORMAT(t.waktu_transaksi, '%Y-%m-01') BETWEEN '2026-01-01' AND '2026-08-31'
  AND NOT EXISTS (
      SELECT 1 FROM produk_hpp_periode h WHERE h.produk_id = p.produk_id
  )
GROUP BY p.produk_id, p.produk_kode, p.produk_nama, s.nama,
         p.produk_harga_beli_terakhir, bd.pembelian_det_produk_harga
ORDER BY qty_terjual DESC;
```

> Hasil verifikasi (Jan–Agu 2026): 46 produk — contoh AL114ZZZZZ, CB140,
> LM010, TRA474, IGM022, BT088, CB464, PG059, CB006, PG007ZZZZ, dst.
> Ganti rentang pada `WHERE` untuk periode lain.

---

## Query 3 — Daftar Lengkap (Union Query 1 + Query 2) untuk Audit

```sql
SELECT
    'HPP_kosong_terjual' AS kategori,
    h.produk_kode,
    h.produk_nama,
    h.satuan_kode,
    s.nama AS satuan_nama,
    h.stok_keluar,
    h.hpp_moving_average AS hpp_sekarang
FROM produk_hpp_periode h
LEFT JOIN satuan s ON s.kode = h.satuan_kode
WHERE h.periode_awal BETWEEN '2026-01-01' AND '2026-08-31'
  AND (h.hpp_moving_average IS NULL OR h.hpp_moving_average = 0)
  AND EXISTS (
      SELECT 1 FROM pos_transaction_items i
      JOIN pos_transactions t ON t.id = i.pos_transaction_id
      WHERE i.product_id = h.produk_id
        AND DATE_FORMAT(t.waktu_transaksi, '%Y-%m-01') = h.periode_awal
  )

UNION ALL

SELECT
    'tidak_ada_di_tabel' AS kategori,
    p.produk_kode,
    p.produk_nama,
    COALESCE(p.produk_satuan_default, p.produk_satuan) AS satuan_id_produk,
    s.nama AS satuan_nama,
    NULL,
    NULL
FROM produk p
LEFT JOIN satuan s ON s.id = p.produk_satuan_default
WHERE NOT EXISTS (
    SELECT 1 FROM produk_hpp_periode h WHERE h.produk_id = p.produk_id
)
  AND EXISTS (
      SELECT 1 FROM pos_transaction_items i
      JOIN pos_transactions t ON t.id = i.pos_transaction_id
      WHERE i.product_id = p.produk_id
        AND DATE_FORMAT(t.waktu_transaksi, '%Y-%m-01') BETWEEN '2026-01-01' AND '2026-08-31'
  )
ORDER BY kategori, produk_kode;
```

---

## Cara Mengisi

Karena HPP **identik per produk** (semua cabang & periode memakai nilai yang
sama), cukup isi per `produk_kode` dengan harga beli yang sesuai
**satuan_kode** pada baris tsb.

### Isi satu produk untuk semua periode & cabang

```sql
UPDATE produk_hpp_periode
SET hpp_moving_average = 15000,   -- ganti harga beli per satuan
    sumber_hpp = 'manual'
WHERE produk_kode = 'KC005'
  AND (hpp_moving_average IS NULL OR hpp_moving_average = 0);
```

> Tanpa filter periode → mengisi semua periode (Jan–Agu) sekaligus, sesuai
> prinsip HPP identik per produk.

### Isi untuk 46 produk yang TIDAK ada di `produk_hpp_periode` (INSERT)

Produk-produk ini belum punya baris sama sekali. Karena HPP identik per produk,
buat satu baris per (produk × cabang × periode) untuk semua periode & cabang
yang ada di database.

**Penting soal satuan:** `produk.produk_satuan_default` memakai id satuan
Brighter (mis. id 1 = "IKT") yang TIDAK cocok dengan `satuan_kode` di
`produk_hpp_periode`. Satuan yang benar = `pos_transaction_items.unit_label`
(dedupe per produk, ambil yang paling sering dipakai):

```sql
INSERT INTO produk_hpp_periode (
    produk_id, produk_kode, produk_nama, satuan_id, satuan_kode,
    cabang_id, cabang_kode, cabang_nama,
    periode_awal, periode_akhir,
    hpp_moving_average, sumber_hpp, synced_at
)
SELECT
    p.produk_id,
    p.produk_kode,
    p.produk_nama,
    su.id,                                  -- id dari tabel satuan
    su.kode,                                -- PCS / LSN / ROL / ...
    cab.id,
    cab.kode,
    cab.nama,
    per.periode_awal,
    per.periode_akhir,
    <harga_beli_per_satuan>,                -- ganti: harga HPP sesuai su.kode
    'manual',
    NOW()
FROM produk p
JOIN (
    -- satuan penjualan yang benar (unit_label), paling sering dipakai
    SELECT product_id,
           unit_label,
           ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY COUNT(*) DESC) rn
    FROM pos_transaction_items
    GROUP BY product_id, unit_label
) u ON u.product_id = p.produk_id AND u.rn = 1
JOIN satuan su ON su.kode = u.unit_label
JOIN (
    -- periode Jan–Agu 2026 (atau seluruh periode yang sudah ada di tabel)
    SELECT DISTINCT periode_awal, periode_akhir
    FROM produk_hpp_periode
    WHERE periode_awal BETWEEN '2026-01-01' AND '2026-08-31'
) per
CROSS JOIN (
    SELECT id, kode, nama FROM cabang
) cab
WHERE NOT EXISTS (
    SELECT 1 FROM produk_hpp_periode h WHERE h.produk_id = p.produk_id
)
  AND EXISTS (
      SELECT 1 FROM pos_transaction_items i
      JOIN pos_transactions t ON t.id = i.pos_transaction_id
      WHERE i.product_id = p.produk_id
        AND DATE_FORMAT(t.waktu_transaksi, '%Y-%m-01') BETWEEN '2026-01-01' AND '2026-08-31'
  );
```

> `satuan_id` di atas memakai id tabel `satuan` (konsisten dengan
> `satuan_kode`). Ini berbeda dari `satuan_id` asli di baris lain
> `produk_hpp_periode` (yang memakai id Brighter), tapi `sp_fin_dash_hpp`
> hanya membaca `hpp_moving_average`, jadi `satuan_kode` lah yang penting
> sebagai label. Untuk harga, ganti literal `<harga_beli_per_satuan>` — atau
> gunakan `CASE p.produk_kode ... END` bila masing-masing produk berbeda.

### Isi sekaligus untuk banyak produk (pakai CASE)

```sql
UPDATE produk_hpp_periode
SET hpp_moving_average = CASE produk_kode
    WHEN 'KC005' THEN 15000
    WHEN 'TX028' THEN 42000
    WHEN 'BS010' THEN 35000
    -- dst ...
END,
    sumber_hpp = 'manual'
WHERE produk_kode IN ('KC005','TX028','BS010', ...)
  AND (hpp_moving_average IS NULL OR hpp_moving_average = 0);
```

**PENTING:**
- Nilai HPP harus sesuai `satuan_kode` baris tsb (mis. `BS010` = STF/STAF,
  `KR046` = KTN, `KC249` = SET, `KP009` = MTR).
- Skema `produk_hpp_periode` di-deploy oleh user (bukan via artisan migrate).
  Update data di atas boleh dijalankan langsung oleh user — tidak perlu
  `php artisan migrate`.
