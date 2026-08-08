SELECT 
    kb.akun_kode_snapshot AS kode_akun,
    kb.akun_nama_snapshot AS nama_akun,
    -- Total Penerimaan Lainnya (selain pelunasan piutang karyawan)
    SUM(CASE WHEN kbd.kas_kategori_id != 3 THEN kbd.nominal ELSE 0 END) AS total_penerimaan_lain,
    -- Total Penerimaan Pelunasan Piutang Karyawan (kas_kategori_id = 3)
    SUM(CASE WHEN kbd.kas_kategori_id = 3 THEN kbd.nominal ELSE 0 END) AS total_penerimaan_piutang_karyawan,
    -- Grand Total Kas Masuk
    SUM(kbd.nominal) AS grand_total
FROM csb_db.kas_bank kb
JOIN csb_db.kas_bank_detail kbd ON kb.id = kbd.kas_bank_id
WHERE kb.tipe = 'masuk' 
  AND kb.cabang_id = 1
  AND kb.legacy_kasbank_id IS NOT NULL
  AND kb.tanggal BETWEEN '2026-01-01' AND '2026-08-30'
GROUP BY kb.akun_cashbank_id, kb.akun_kode_snapshot, kb.akun_nama_snapshot;

SELECT 
    kb.akun_kode_snapshot AS kode_akun,
    kb.akun_nama_snapshot AS nama_akun,
    COALESCE(kbd.kas_kategori_id, kbd.penerimaan_id) AS kategori_atau_penerimaan_id,
    COALESCE(kk.nama, kp.penerimaan_nama, 'Penerimaan Lain (Jalur A)') AS nama_kategori,
    SUM(kbd.nominal) AS total_nominal
FROM csb_db.kas_bank kb
JOIN csb_db.kas_bank_detail kbd ON kb.id = kbd.kas_bank_id
LEFT JOIN csb_db.kas_kategori kk ON kk.id = kbd.kas_kategori_id
LEFT JOIN csb_db.master_keuangan_penerimaan kp ON kp.penerimaan_id = kbd.penerimaan_id
WHERE kb.tipe = 'masuk' 
  AND kb.cabang_id = 1
  AND kb.legacy_kasbank_id IS NOT NULL
  AND kb.tanggal BETWEEN '2026-01-01' AND '2026-08-30'
GROUP BY 
    kb.akun_cashbank_id, 
    kb.akun_kode_snapshot, 
    kb.akun_nama_snapshot, 
    kbd.kas_kategori_id, 
    kbd.penerimaan_id, 
    kk.nama, 
    kp.penerimaan_nama
ORDER BY kb.akun_kode_snapshot, total_nominal DESC;