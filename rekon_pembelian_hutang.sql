-- =====================================================================
-- REKON PEMBELIAN vs PELUNASAN HUTANG (csb_db)
-- Semua query read-only. Header pelunasan pakai kolom `stat_dok`,
-- faktur pembelian pakai `status_dok`.
--
-- Kebijakan status dokumen:
--   * PERHITUNGAN REKON hanya memakai status 'Tertutup' (dokumen sah).
--   * Data 'Batal' tetap DITAMPILKAN sebagai kolom referensi historical
--     (faktur_btl/net_btl/lunas_btl/bayar_btl) tetapi TIDAK dijumlah ke
--     dalam angka selisih rekon.
-- =====================================================================

-- =====================================================================
-- RINGKASAN PER CABANG
-- Kolom *_tt  = rekon (Tertutup saja): faktur/net/bayar/sisa pembelian,
--               lunas/bayar_lh header pelunasan, detail/sum_det detail
--               pelunasan (join header Tertutup, non-sampah).
-- slsh_agr    = bayar_lh - bayar faktur;  slsh_lh = bayar_lh - sum_det.
-- Kolom *_btl = INFO Batal (historical, tidak dipakai di rekon).
-- =====================================================================
SELECT p.cabang_id,
       p.faktur_tt, p.net_tt, p.bayar_tt, p.sisa_tt,
       COALESCE(h.lunas_tt,0) lunas_tt, COALESCE(h.bayar_lh_tt,0) bayar_lh_tt,
       COALESCE(d.detail_tt,0) detail_tt, COALESCE(d.sum_det_tt,0) sum_det_tt,
       COALESCE(h.bayar_lh_tt,0) - p.bayar_tt slsh_agr,
       COALESCE(h.bayar_lh_tt,0) - COALESCE(d.sum_det_tt,0) slsh_lh,
       pb.faktur_btl, pb.net_btl,
       hb.lunas_btl, COALESCE(hb.bayar_btl,0) bayar_btl
FROM (SELECT cabang_id, COUNT(*) faktur_tt, ROUND(SUM(total_net_rp)) net_tt,
             ROUND(SUM(total_bayar_rp)) bayar_tt, ROUND(SUM(total_sisa_rp)) sisa_tt
      FROM brighter_persediaan_pembelian WHERE status_dok='Tertutup'
      GROUP BY cabang_id) p
LEFT JOIN (SELECT cabang_id, COUNT(*) lunas_tt, ROUND(SUM(bayar)) bayar_lh_tt
           FROM brighter_transaksi_pelunasan_hutang WHERE stat_dok='Tertutup'
           GROUP BY cabang_id) h ON h.cabang_id = p.cabang_id
LEFT JOIN (SELECT h.cabang_id, COUNT(*) detail_tt, ROUND(SUM(d.nilai_bayar)) sum_det_tt
           FROM brighter_transaksi_pelunasan_hutang_detail d
           JOIN brighter_transaksi_pelunasan_hutang h ON h.id = d.master_lunas_id
           WHERE h.stat_dok='Tertutup' AND d.master_lunas_id IS NOT NULL
           GROUP BY h.cabang_id) d ON d.cabang_id = p.cabang_id
LEFT JOIN (SELECT cabang_id, COUNT(*) faktur_btl, ROUND(SUM(COALESCE(total_net_rp,0))) net_btl
           FROM brighter_persediaan_pembelian WHERE status_dok='Batal'
           GROUP BY cabang_id) pb ON pb.cabang_id = p.cabang_id
LEFT JOIN (SELECT cabang_id, COUNT(*) lunas_btl, ROUND(SUM(bayar)) bayar_btl
           FROM brighter_transaksi_pelunasan_hutang WHERE stat_dok='Batal'
           GROUP BY cabang_id) hb ON hb.cabang_id = p.cabang_id
ORDER BY p.cabang_id;

-- =====================================================================
-- CEK 1 — Faktur Tertutup inkonsisten internal (sisa != net - bayar)
-- =====================================================================
SELECT id, cabang_id, nobukti, total_net_rp, total_bayar_rp, total_sisa_rp
FROM brighter_persediaan_pembelian
WHERE status_dok='Tertutup'
  AND ABS(COALESCE(total_sisa_rp,0)
        - (COALESCE(total_net_rp,0) - COALESCE(total_bayar_rp,0))) > 0.5
ORDER BY cabang_id, id;

-- =====================================================================
-- CEK 2 — Faktur Tertutup direferensikan >1x oleh detail pelunasan
-- (detail dari header Tertutup, faktur Tertutup)
-- =====================================================================
SELECT d.master_hutang_data_pembelian_id faktur_id, COUNT(*) referensi,
       COUNT(DISTINCT d.master_lunas_id) jml_pelunasan, ROUND(SUM(d.nilai_bayar)) total_detail
FROM brighter_transaksi_pelunasan_hutang_detail d
JOIN brighter_transaksi_pelunasan_hutang h ON h.id = d.master_lunas_id
JOIN brighter_persediaan_pembelian p ON p.id = d.master_hutang_data_pembelian_id
WHERE h.stat_dok='Tertutup' AND p.status_dok='Tertutup'
  AND d.master_hutang_data_pembelian_id IS NOT NULL
GROUP BY d.master_hutang_data_pembelian_id
HAVING COUNT(*) > 1
ORDER BY referensi DESC, total_detail DESC;

-- =====================================================================
-- CEK 3 — Header pelunasan Tertutup `bayar` vs `SUM(detail)`
-- =====================================================================
SELECT h.id, h.nobukti, h.cabang_id, h.bayar,
       COALESCE(SUM(d.nilai_bayar),0) sum_detail,
       h.bayar - COALESCE(SUM(d.nilai_bayar),0) selisih
FROM brighter_transaksi_pelunasan_hutang h
LEFT JOIN brighter_transaksi_pelunasan_hutang_detail d ON d.master_lunas_id = h.id
WHERE h.stat_dok='Tertutup'
GROUP BY h.id, h.bayar, h.nobukti, h.cabang_id
HAVING ABS(COALESCE(SUM(d.nilai_bayar),0) - h.bayar) > 0.5
ORDER BY ABS(h.bayar - COALESCE(SUM(d.nilai_bayar),0)) DESC;

-- =====================================================================
-- CEK 4 — Faktur Tertutup tanpa detail pelunasan
-- =====================================================================
-- Hitung + sisa total
SELECT COUNT(*) faktur_tanpa_detail, ROUND(SUM(total_sisa_rp)) sisa_total
FROM brighter_persediaan_pembelian p
WHERE p.status_dok='Tertutup'
  AND NOT EXISTS (SELECT 1 FROM brighter_transaksi_pelunasan_hutang_detail d
                  WHERE d.master_hutang_data_pembelian_id = p.id);

-- Faktur Lunas tapi tanpa detail (prioritas)
SELECT p.id, p.nobukti, p.cabang_id, p.supplier_data_supplier_nama, p.total_net_rp
FROM brighter_persediaan_pembelian p
WHERE p.status_dok='Tertutup' AND p.status_lunas='Lunas' AND COALESCE(p.total_sisa_rp,0)=0
  AND NOT EXISTS (SELECT 1 FROM brighter_transaksi_pelunasan_hutang_detail d
                  WHERE d.master_hutang_data_pembelian_id = p.id)
ORDER BY p.cabang_id, p.id;

-- =====================================================================
-- CEK 5 — Faktur Tertutup overpaid (sisa < 0)
-- =====================================================================
SELECT id, cabang_id, nobukti, total_net_rp, total_bayar_rp, total_sisa_rp
FROM brighter_persediaan_pembelian
WHERE status_dok='Tertutup' AND total_sisa_rp < -0.5
ORDER BY total_sisa_rp ASC;

-- =====================================================================
-- CEK 6 — Record sampah detail pelunasan (NULL / id-hash)
-- =====================================================================
SELECT d.id, d.fhutang_id, d.master_lunas_id, d.master_hutang_id, d.nilai_bayar, d.cabang_id
FROM brighter_transaksi_pelunasan_hutang_detail d
WHERE d.master_lunas_id IS NULL OR d.nilai_bayar IS NULL;

-- =====================================================================
-- LAMPIRAN (INFO HISTORICAL) — data Batal, hanya referensi, bukan rekon
-- =====================================================================
-- Faktur Batal tanpa detail pelunasan
SELECT COUNT(*) faktur_batal, ROUND(SUM(COALESCE(total_sisa_rp,0))) sisa_batal
FROM brighter_persediaan_pembelian WHERE status_dok='Batal';

-- Header pelunasan Batal tanpa detail
SELECT COUNT(*) header_batal
FROM brighter_transaksi_pelunasan_hutang h
WHERE h.stat_dok='Batal'
  AND NOT EXISTS (SELECT 1 FROM brighter_transaksi_pelunasan_hutang_detail d
                  WHERE d.master_lunas_id = h.id);
