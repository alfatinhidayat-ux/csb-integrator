# Pipeline Pelunasan Hutang & Rekon (csb_db)

Dokumentasi resmi untuk pipeline **hutang pembelian + pelunasan hutang** (supplier):

- Sumber: API Brighter (`/persediaan/pembelian`, `/transaksi/pelunasan_hutang`, dll)
- Target: database **`csb_db`** (bukan `brighter_mirror`)
- Acuan resmi kualitas data: **laporan PDF 101 Brighter** (rekap `lap_hutang_*.pdf` + detail `det_*.pdf`)
- Tujuan akhir: menyiapkan data staging yang bersih untuk **fitur pelunasan hutang** yang akan dibangun di aplikasi CSB.

---

## 1. Menjalankan ulang (urutan wajib)

Semua perintah dijalankan dari folder project. Konfigurasi DB `csb_db` dibaca dari `.env`
(variabel `CSB_DB_*`; jika kosong memakai kredensial `BRIGHTER_DB_*` pada server MySQL yang sama).

### Langkah 1 — Refesh staging dari API

```bash
python sync_finance.py --env                     # semua cabang aktif (auto-discover)
python sync_finance.py --env --cabang-ids 1,5    # cabang tertentu (dipisah koma)
python sync_finance.py --env --workers 10        # paralel fetch child (default 5)
```

Yang disinkronkan (7 langkah per cabang):

| # | Isi | Tabel target |
|---|-----|--------------|
| 0 | Master Supplier (upsert per server, `cabang_id` selalu 1) | `supplier` |
| 1 | Faktur Pembelian header | `brighter_persediaan_pembelian` |
| 2 | Header Pelunasan Hutang | `brighter_transaksi_pelunasan_hutang` |
| 3 | Detail Pelunasan Hutang (concurrent per header) | `brighter_transaksi_pelunasan_hutang_detail` |
| 4 | Foto Pelunasan Hutang (concurrent per header) | `brighter_transaksi_pelunasan_hutang_foto` |
| 5 | Header Pelunasan Piutang | `brighter_transaksi_pelunasan_piutang` |
| 6 | Piutang Customer Detail (filter faktur >= 2024-01-01) | `brighter_transaksi_piutang_customer_detail` |

> ⚠️ **VOLATILE**: `sync_finance.py` menimpa data dari API (upsert). Koreksi manual apa pun
> yang pernah dilakukan ke tabel staging **akan tertimpa** saat script dijalankan. Itulah
> sebabnya langkah 2 (koreksi Batal) WAJIB dijalankan SETELAH setiap sync.

### Langkah 2 — Koreksi faktur Lunas yang pelunasannya Batal (WAJIB setelah langkah 1)

```bash
python fix_hutang_lunas_pelunasan_batal.py         # dry-run, cek daftar dulu
python fix_hutang_lunas_pelunasan_batal.py --apply # eksekusi & commit
```

Normalisasi faktur `Tertutup` + `Lunas` yang **semua** pelunasannya berstatus `Batal`
(kasus PT. GUNUNG AGUNG SENTOSA, cabang 5) menjadi `total_bayar_rp = total_net_rp`,
`total_sisa_rp = 0`. Opsi: `--supplier "NAMA"` untuk supplier lain, `--limit N` untuk debug.

Idempoten — hanya memproses faktur yang masih `sisa > 0`.

### Langkah 3 — Rekon konsistensi (read-only)

```bash
python rekon_pembelian_hutang.py --env                 # 6 cek pembelian vs pelunasan
python rekon_pembelian_hutang.py --env --cabang-ids 1,5
python rekon_pinjaman_karyawan.py --env                # rekon pinjaman vs kas/bank
```

### Langkah 4 — Verifikasi vs PDF resmi (SQL, read-only)

Jalankan blok query di **`rekon_pelunasan_pdf.sql`** (5 blok + query acuan):

1. Header pelunasan `bayar != SUM(detail)` (sebelumnya 58 baris)
2. Faktur dirujuk >1× oleh detail pelunasan (sebelumnya 90 — bukan error, pembayaran bertahap)
3. Faktur overpaid `sisa < 0` (sebelumnya 86 — kondisi resmi API, muncul 2+ baris di PDF)
4. Faktur tanpa detail pelunasan (sebelumnya 715 — semua faktur ADA di PDF)
5. Faktur yang tidak ada di PDF detail (15, cbg 5 — pelunasan Batal GUNUNG AGUNG)

> Untuk validasi per-faktur vs PDF, ekstraksi PDF berada di `det_txt/det_*.pdf.txt`
> (`extract_det.py` + `parse_det_pdf.py`), output ter-parse: `det_txt/det_parsed.py`.

---

## 2. Jaminan tidak duplikat (idempotency)

Semua tabel data aman dijalankan berulang:

| Mekanisme | Keterangan |
|-----------|------------|
| PK `(id, cabang_id)` | Semua tabel data punya primary key — tidak ada baris dobel |
| UPSERT (`ON DUPLICATE KEY UPDATE`) | `db.upsert_records()` hanya plain INSERT untuk tabel tanpa `id`; tabel pelunasan/pembelian punya `id` → upsert |
| `id` deterministik (MD5) | Record API tanpa `id` dibuatkan id hash dari isi record + `cabang_id` (`map_record()` di `sync_finance.py`) → upsert berulang menghasilkan record yang sama, bukan dobel |
| `supplier` upsert-only | Tidak pernah di-truncate; `authenticated_user_id` dipertahankan, user supplier baru dibuat hanya jika belum ada |
| Tanpa truncate | `sync_finance.py` tidak pernah truncate; koreksi (mis. fix Batal) dipertahankan sampai sync berikutnya |
| Orphan piutang | Satu-satunya `DELETE` (by design): record piutang customer yang `lpiutang_id`-nya tidak lagi ada di API |
| Script koreksi idempoten | `fix_hutang_lunas_pelunasan_batal.py` hanya memproses faktur `sisa > 0` |

---

## 3. Model data inti

```
supplier (id)
   ▲
   └─ brighter_transaksi_pelunasan_hutang (supp)         → header pelunasan
        ├─ brighter_transaksi_pelunasan_hutang_detail (master_lunas_id = h.id)
        │     └─ master_hutang_data_pembelian_id = p.id  → faktur pembelian
        └─ brighter_transaksi_pelunasan_hutang_foto (fhutang_id = h.id)

brighter_persediaan_pembelian (id)                       → faktur pembelian header
   └─ brighter_persediaan_pembelian_detail (pembelian_det_master_id = p.id)
```

### Kolom kunci

| Tabel | Kolom penting |
|-------|---------------|
| `brighter_persediaan_pembelian` | `id`, `nobukti`, `cabang_id`, `total_net_rp` (net setelah diskon item), `total_bayar_rp`, `total_sisa_rp`, `status_dok` (`'Tertutup'`), `status_lunas`, `supplier_data_supplier_nama`, `tanggal` |
| `brighter_persediaan_pembelian_detail` | `pembelian_det_master_id` → header, `pembelian_det_subtotal_rp` (**gross**), `pembelian_det_subtotal_net_rp` (**net**), `pembelian_det_diskon_rp`, `pembelian_det_qty_diterima`, `produk_nama` |
| `brighter_transaksi_pelunasan_hutang` | `id`, `nobukti`, `cabang_id`, `supp` (supplier), `tanggal`, `bayar`, `cara`, `akun`, **`stat_dok`** (bukan `status_dok`!) |
| `brighter_transaksi_pelunasan_hutang_detail` | `id`, `master_lunas_id` → header pelunasan, `master_hutang_data_pembelian_id` → faktur pembelian, `nilai_bayar`, `hutang_awal`, `terbayar`, `sisa_bayar` |
| `supplier` | `id` (= `supplier_id` Brighter), `nama`, `kode`, `aktif`, `npwp`, `jatuh_tempo`, `cabang_id_ref_bright` |

---

## 4. Query rekon yang benar (basis GROSS — cocok PDF)

> ⚠️ **Jangan pakai `SUM(total_net_rp)` sebagai `hutang_awal`.** PDF menghitung
> **HUTANG AWAL** atas nilai **gross** (sebelum diskon per item), dan **TERBAYAR** =
> bayar aktual + diskon item. Selisihnya untuk cabang 1 = 101.277.397 (total diskon
> item). Kolom **SISA** (`total_sisa_rp`) tidak terpengaruh — itu yang setara staging.
> Hasil cbg 1: 1.476.320.570 / 1.254.345.383 (±1) / 221.975.188 = PDF.

```sql
SELECT p.cabang_id,
       COUNT(DISTINCT p.id)                                 AS jumlah_faktur,
       ROUND(SUM(g.gross))                                   AS hutang_awal,
       ROUND(SUM(p.total_bayar_rp) + SUM(g.gross - p.total_net_rp)) AS terbayar,
       ROUND(SUM(p.total_sisa_rp))                           AS sisa
FROM brighter_persediaan_pembelian p
JOIN (SELECT pembelian_det_master_id,
             SUM(pembelian_det_subtotal_rp) AS gross
      FROM brighter_persediaan_pembelian_detail
      GROUP BY pembelian_det_master_id) g
  ON g.pembelian_det_master_id = p.id
WHERE p.status_dok = 'Tertutup'
GROUP BY p.cabang_id
ORDER BY p.cabang_id;
```

---

## 5. Panduan untuk fitur pelunasan hutang (yang akan dibangun)

### Sumber data yang dipakai fitur

- **Daftar hutang per supplier**: `brighter_persediaan_pembelian` (`status_dok='Tertutup'`),
  nilai sisa = `total_sisa_rp`.
- **Detail barang per faktur**: `brighter_persediaan_pembelian_detail`.
- **Riwayat pelunasan**: `brighter_transaksi_pelunasan_hutang` (header, filter `stat_dok='Tertutup'`)
  join `..._detail` via `master_lunas_id`.
- **Bukti foto**: `brighter_transaksi_pelunasan_hutang_foto`.

### Kaidah join yang benar

- Detail pelunasan → header: `d.master_lunas_id = h.id` (bukan `pelunasan_hutang_id`).
- Detail pelunasan → faktur: `d.master_hutang_data_pembelian_id = p.id`.
- Status header pelunasan pakai `h.stat_dok`; status faktur pakai `p.status_dok`.

### Caveat / isu data yang sudah diverifikasi

1. **Pelunasan Batal** (GUNUNG AGUNG, cbg 5): faktur `Lunas` tapi pelunasannya `Batal`
   → staging `bayar=0, sisa=net` sampai `fix_hutang_lunas_pelunasan_batal.py --apply`
   dijalankan. **Fitur harus mengabaikan pelunasan `stat_dok='Batal'`.**
2. **Staging volatile**: data bisa tertimpa oleh `sync_finance.py`. Jika fitur menulis ke
   tabel ini, lindungi kolom yang dikelola sistem (mis. dengan kolom terpisah), karena
   seluruh tabel ber-`id` deterministik + upsert.
3. **Header vs detail pelunasan**: 58 header `bayar != SUM(detail)`; 90 faktur dirujuk
   >1× (pembayaran bertahap — wajar, satu faktur punya banyak baris pelunasan).
4. **Faktur tanpa detail pelunasan**: 715 faktur (tercatat di PDF, hanya detail pelunasan
   staging yang belum ada) — sisa hutangnya tetap valid lewat `total_sisa_rp`.
5. **Overpaid**: 86 faktur `sisa < 0` — kondisi resmi API (muncul 2+ baris di PDF).
6. **Gross vs net**: kolom nominal di faktur adalah **net** (setelah diskon item); PDF
   menampilkan gross. Jangan campur keduanya dalam satu perhitungan.

### Rekomendasi untuk fitur

- Gunakan `total_sisa_rp` sebagai nilai hutang belum dibayar; hitung ulang dari detail
  pelunasan hanya untuk validasi, bukan sebagai sumber utama.
- Untuk laporan/validasi internal, selalu bandingkan dengan query acuan di Section 4.
- Setelah setiap import/sync, jalankan urutan Langkah 1 → 2 → 3 → 4 di atas sebelum
  fitur membaca data.
