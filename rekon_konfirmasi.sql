-- ============================================================
-- KONFIRMASI DATA MIGRASI PINJAMAN KARYAWAN vs KAS/BANK
-- Semua query memakai SELECT (read-only), tinggal jalankan di
-- database csb_db dan hasilnya bisa ditunjukkan ke user.
-- ============================================================

-- 1. DAFTAR LOAN YANG TIDAK KONSISTEN (pelunasan > nilai)
--    termasuk nama karyawan supaya mudah diverifikasi
-- ----------------------------------------------------------
SELECT p.ppinjaman_id,
       p.ppinjaman_cabang_id,
       k.nama AS nama_karyawan,
       p.ppinjaman_no,
       p.ppinjaman_tanggal,
       p.ppinjaman_nilai,
       p.ppinjaman_pelunasan,
       p.ppinjaman_sisa,
       p.ppinjaman_jenis,
       p.ppinjaman_status,
       p.ppinjaman_aktif
FROM pinjaman_karyawan p
LEFT JOIN karyawan k ON k.id = p.ppinjaman_karyawan_id
WHERE p.ppinjaman_pelunasan > p.ppinjaman_nilai + 0.5
ORDER BY p.ppinjaman_cabang_id, p.ppinjaman_id;

-- ============================================================
-- 2. DAFTAR SEMUA PEMBAYARAN (CICILAN) LOAN BERMASALAH
--    lengkap dengan bukti kas/bank masuk utk cek cash flow
--    (ubah WHERE sesuai loan yang mau diperiksa, misal = 7)
-- ============================================================
SELECT p.ppinjaman_id,
       p.ppinjaman_cabang_id,
       k.nama AS nama_karyawan,
       p.ppinjaman_no,
       p.ppinjaman_nilai,
       p.ppinjaman_pelunasan,
       p.ppinjaman_sisa,
       h.kasbank_nobukti AS nobukti_bayar,
       h.kasbank_tanggal AS tgl_bayar,
       k2.kdpk_dilunasi AS cicilan_dibayar
FROM pinjaman_karyawan p
LEFT JOIN karyawan k ON k.id = p.ppinjaman_karyawan_id
JOIN akuntansi_kasbank_masuk_piutang_karyawan k2
     ON k2.kdpk_ppinjaman_id = p.ppinjaman_id
JOIN akuntansi_kasbank_masuk h
     ON h.kasbank_id = k2.kdpk_master_id
WHERE p.ppinjaman_id = 7                                  -- ganti sesuai loan
ORDER BY p.ppinjaman_id, h.kasbank_tanggal;

-- ============================================================
-- 3. REKAP DISBURSE (PENCAIRAN) vs NILAI LOAN PER CABANG
--    slsh_nilai = disburse - nilai (selisih utk dikonfirmasi)
-- ============================================================
SELECT A.cabang_id,
       A.jml_loan,
       A.nilai_total,
       B.jml_cair,
       B.disburse_total,
       (COALESCE(B.disburse_total, 0) - A.nilai_total) AS slsh_nilai
FROM (
    SELECT ppinjaman_cabang_id AS cabang_id,
           COUNT(ppinjaman_id) AS jml_loan,
           SUM(ppinjaman_nilai) AS nilai_total
    FROM pinjaman_karyawan
    GROUP BY ppinjaman_cabang_id
) A
LEFT JOIN (
    SELECT kasbank_cabang_id AS cabang_id,
           COUNT(kasbank_id) AS jml_cair,
           SUM(kasbank_pengeluaran_pinjaman_karyawan) AS disburse_total
    FROM akuntansi_kasbank_keluar
    WHERE kasbank_pengeluaran_pinjaman_karyawan > 0
    GROUP BY kasbank_cabang_id
) B ON B.cabang_id = A.cabang_id
ORDER BY A.cabang_id;

-- ============================================================
-- 4. CARI PENCAIRAN (KAS/BANK KELUAR) YANG TIDAK ADA LOANNYA
--    kemungkinan pencairan gabungan / nilai berubah setelah cair
-- ============================================================
SELECT kk.kasbank_id,
       kk.kasbank_cabang_id,
       kk.kasbank_nobukti,
       kk.kasbank_tanggal,
       kk.kasbank_pengeluaran_pinjaman_karyawan AS nominal_cair
FROM akuntansi_kasbank_keluar kk
WHERE kk.kasbank_pengeluaran_pinjaman_karyawan > 0
  AND kk.kasbank_cabang_id = 1                            -- ubah sesuai cabang
  AND kk.kasbank_pengeluaran_pinjaman_karyawan NOT IN (
      SELECT ppinjaman_nilai FROM pinjaman_karyawan
      WHERE ppinjaman_cabang_id = 1
  )
ORDER BY kk.kasbank_tanggal;