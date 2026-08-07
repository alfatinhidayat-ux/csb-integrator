-- =====================================================================
-- VERIFIKASI PELUNASAN HUTANG vs PDF DETAIL (csb_db, read-only)
--
-- Latar belakang: laporan resmi PDF "Hutang Pembelian - Opsi Cetak:
-- Detail" sudah diparse dan dibandingkan per-faktur dengan staging.
-- Hasilnya:
--   * 2,914 / 3,046 faktur staging match persis dengan PDF (sisa cocok).
--   * Faktur overpaid (sisa<0) = faktur yang muncul 2+ baris di PDF
--     (pembayaran bertahap) -> kondisi resmi data API, bukan error.
--   * 15 faktur kairatu tidak ada di PDF detail = faktur dengan
--     pelunasan Batal (kasus GUNUNG AGUNG SENTOSA yang sudah dikoreksi).
--   * Semua faktur "tanpa detail" staging ADA di PDF detail.
--
-- Empat isu kualitas di bawah ini adalah masalah tabel PELUNASAN
-- (brighter_transaksi_pelunasan_hutang), bukan nilai faktur.
-- =====================================================================

-- ------------------------------------------------------------
-- 0. QUERY ACUAN REKON PER CABANG (cocok PDF DETAIL - RESMI)
--
-- PENTING: PDF kolom HUTANG AWAL & TERBAYAR dihitung atas nilai GROSS
-- (sebelum diskon per item), bukan `total_net_rp`.
--   * HUTANG AWAL (PDF) = SUM(pembelian_det_subtotal_rp) dari tabel detail
--   * TERBAYAR (PDF)    = total_bayar_rp + diskon item
--                         (diskon item dianggap sebagai "terbayar")
--   * SISA (PDF)        = total_sisa_rp  -> satu-satunya yang setara staging
--
-- Jangan pakai `SUM(p.total_net_rp)` untuk kolom hutang_awal: nilainya
-- lebih rendah dari PDF sebesar total diskon item (cbg 1: 101,277,397).
-- Hasil cbg 1: 1,476,320,570 / 1,254,345,383 (±1) / 221,975,188 = PDF.
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- 1. HEADER bayar != SUM(detail)  (sebelumnya 58 baris)
--    PDF detail = list per-pelunasan -> header ini yang paling
--    relevan untuk cek silang. `selisih` harus 0 di data ideal.
-- ------------------------------------------------------------
SELECT h.id, h.nobukti, h.cabang_id, h.tanggal,
       ROUND(h.bayar) bayar,
       ROUND(COALESCE(SUM(d.nilai_bayar),0)) sum_detail,
       ROUND(h.bayar - COALESCE(SUM(d.nilai_bayar),0)) selisih
FROM brighter_transaksi_pelunasan_hutang h
LEFT JOIN brighter_transaksi_pelunasan_hutang_detail d
       ON d.master_lunas_id = h.id
WHERE h.stat_dok = 'Tertutup'
GROUP BY h.id, h.bayar, h.nobukti, h.cabang_id, h.tanggal
HAVING ABS(COALESCE(SUM(d.nilai_bayar),0) - h.bayar) > 0.5
ORDER BY ABS(h.bayar - COALESCE(SUM(d.nilai_bayar),0)) DESC;

-- 1b. hitung jumlahnya saja
SELECT COUNT(*) jumlah_header_selisih
FROM (
  SELECT h.id
  FROM brighter_transaksi_pelunasan_hutang h
  LEFT JOIN brighter_transaksi_pelunasan_hutang_detail d
         ON d.master_lunas_id = h.id
  WHERE h.stat_dok = 'Tertutup'
  GROUP BY h.id, h.bayar
  HAVING ABS(COALESCE(SUM(d.nilai_bayar),0) - h.bayar) > 0.5
) x;

-- ------------------------------------------------------------
-- 2. FAKTUR dirujuk >1x oleh detail pelunasan  (sebelumnya 90)
--    = faktur dengan 2+ baris di PDF detail (pembayaran bertahap).
--    Bukan error - konfirmasi PDF menampilkan per-pelunasan.
-- ------------------------------------------------------------
SELECT d.master_hutang_data_pembelian_id faktur_id,
       p.nobukti, p.cabang_id,
       COUNT(*) referensi,
       COUNT(DISTINCT d.master_lunas_id) jml_pelunasan,
       ROUND(SUM(d.nilai_bayar)) total_detail
FROM brighter_transaksi_pelunasan_hutang_detail d
JOIN brighter_transaksi_pelunasan_hutang h ON h.id = d.master_lunas_id
JOIN brighter_persediaan_pembelian p ON p.id = d.master_hutang_data_pembelian_id
WHERE h.stat_dok = 'Tertutup' AND p.status_dok = 'Tertutup'
  AND d.master_hutang_data_pembelian_id IS NOT NULL
GROUP BY d.master_hutang_data_pembelian_id, p.nobukti, p.cabang_id
HAVING COUNT(*) > 1
ORDER BY referensi DESC, total_detail DESC;

-- 2b. hitung jumlahnya saja
SELECT COUNT(*) jumlah_faktur_dirujuk_lebih_1x
FROM (
  SELECT d.master_hutang_data_pembelian_id
  FROM brighter_transaksi_pelunasan_hutang_detail d
  JOIN brighter_transaksi_pelunasan_hutang h ON h.id = d.master_lunas_id
  JOIN brighter_persediaan_pembelian p ON p.id = d.master_hutang_data_pembelian_id
  WHERE h.stat_dok = 'Tertutup' AND p.status_dok = 'Tertutup'
    AND d.master_hutang_data_pembelian_id IS NOT NULL
  GROUP BY d.master_hutang_data_pembelian_id
  HAVING COUNT(*) > 1
) x;

-- ------------------------------------------------------------
-- 3. FAKTUR OVERPAID (sisa < 0)  (sebelumnya 86)
--    Konfirmasi PDF: semua faktur ini muncul 2+ baris di PDF detail
--    dengan pola sisa negatif yang sama -> kondisi resmi data API.
-- ------------------------------------------------------------
SELECT id, cabang_id, nobukti,
       ROUND(total_net_rp) net, ROUND(total_bayar_rp) bayar,
       ROUND(total_sisa_rp) sisa
FROM brighter_persediaan_pembelian
WHERE status_dok = 'Tertutup' AND total_sisa_rp < -0.5
ORDER BY total_sisa_rp ASC;

-- 3b. hitung + total per cabang
SELECT cabang_id, COUNT(*) jumlah, ROUND(SUM(total_sisa_rp)) total_overpaid
FROM brighter_persediaan_pembelian
WHERE status_dok = 'Tertutup' AND total_sisa_rp < -0.5
GROUP BY cabang_id ORDER BY cabang_id;

-- ------------------------------------------------------------
-- 4. FAKTUR tanpa detail pelunasan  (sebelumnya 715)
--    Konfirmasi PDF: SEMUA faktur ini ADA di PDF detail (faktur
--    tercatat; yang kurang adalah detail pelunasan di staging).
-- ------------------------------------------------------------
-- 4a. hitung + sisa total
SELECT COUNT(*) faktur_tanpa_detail, ROUND(SUM(total_sisa_rp)) sisa_total
FROM brighter_persediaan_pembelian p
WHERE p.status_dok = 'Tertutup'
  AND NOT EXISTS (SELECT 1 FROM brighter_transaksi_pelunasan_hutang_detail d
                  WHERE d.master_hutang_data_pembelian_id = p.id);

-- 4b. per cabang
SELECT p.cabang_id, COUNT(*) jumlah, ROUND(SUM(p.total_sisa_rp)) sisa_total
FROM brighter_persediaan_pembelian p
WHERE p.status_dok = 'Tertutup'
  AND NOT EXISTS (SELECT 1 FROM brighter_transaksi_pelunasan_hutang_detail d
                  WHERE d.master_hutang_data_pembelian_id = p.id)
GROUP BY p.cabang_id ORDER BY p.cabang_id;

-- 4c. daftar lengkap (untuk cek silang manual vs PDF)
SELECT p.id, p.cabang_id, p.nobukti, p.supplier_data_supplier_nama,
       ROUND(p.total_net_rp) net, ROUND(p.total_bayar_rp) bayar,
       ROUND(p.total_sisa_rp) sisa, p.status_lunas
FROM brighter_persediaan_pembelian p
WHERE p.status_dok = 'Tertutup'
  AND NOT EXISTS (SELECT 1 FROM brighter_transaksi_pelunasan_hutang_detail d
                  WHERE d.master_hutang_data_pembelian_id = p.id)
ORDER BY p.cabang_id, p.id;

-- ------------------------------------------------------------
-- 5. REFERENSI - faktur yang TIDAK ada di PDF detail (15, cbg 5)
--    = faktur dengan pelunasan Batal (kasus GUNUNG AGUNG yang
--    dikoreksi). Ini yang membuat staging dan PDF tidak match 1:1.
-- ------------------------------------------------------------
SELECT p.id, p.cabang_id, p.nobukti, p.status_lunas,
       ROUND(p.total_bayar_rp) bayar, ROUND(p.total_sisa_rp) sisa,
       h.nobukti no_pelunasan, h.bayar, h.stat_dok status_pelunasan
FROM brighter_persediaan_pembelian p
JOIN brighter_transaksi_pelunasan_hutang_detail d
     ON d.master_hutang_data_pembelian_id = p.id
JOIN brighter_transaksi_pelunasan_hutang h ON h.id = d.master_lunas_id
WHERE p.status_dok = 'Tertutup' AND h.stat_dok = 'Batal'
ORDER BY p.cabang_id, p.id;
