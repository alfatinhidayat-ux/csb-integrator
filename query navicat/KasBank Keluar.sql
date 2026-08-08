SELECT 
    kb.akun_kode_snapshot AS kode_akun,
    kb.akun_nama_snapshot AS nama_akun,
    -- Total Pengeluaran Lainnya (selain pengeluaran pinjaman karyawan)
    SUM(CASE WHEN kbd.kas_kategori_id != 9 THEN kbd.nominal ELSE 0 END) AS total_pengeluaran_lain,
    -- Total Pengeluaran Pinjaman Karyawan (kas_kategori_id = 9)
    SUM(CASE WHEN kbd.kas_kategori_id = 9 THEN kbd.nominal ELSE 0 END) AS total_pengeluaran_pinjaman_karyawan,
    -- Grand Total Kas Keluar
    SUM(kbd.nominal) AS grand_total
FROM csb_db.kas_bank kb
JOIN csb_db.kas_bank_detail kbd ON kb.id = kbd.kas_bank_id
WHERE kb.tipe = 'keluar' 
  AND kb.cabang_id = 1
  AND kb.legacy_kasbank_id IS NOT NULL
  AND kb.tanggal BETWEEN '2026-01-01' AND '2026-08-30'
GROUP BY kb.akun_cashbank_id, kb.akun_kode_snapshot, kb.akun_nama_snapshot;

SELECT 
    kb.akun_kode_snapshot AS kode_akun,
    kb.akun_nama_snapshot AS nama_akun,
    COALESCE(kbd.kas_kategori_id, kbd.pengeluaran_id) AS kategori_atau_pengeluaran_id,
    COALESCE(kk.nama, kp.pengeluaran_nama, 'Pengeluaran Lain (Jalur A)') AS nama_kategori_pengeluaran,
    SUM(kbd.nominal) AS total_nominal
FROM csb_db.kas_bank kb
JOIN csb_db.kas_bank_detail kbd ON kb.id = kbd.kas_bank_id
LEFT JOIN csb_db.kas_kategori kk ON kk.id = kbd.kas_kategori_id
LEFT JOIN csb_db.master_keuangan_pengeluaran kp ON kp.pengeluaran_id = kbd.pengeluaran_id
WHERE kb.tipe = 'keluar' 
  AND kb.cabang_id = 1
  AND kb.legacy_kasbank_id IS NOT NULL
  AND kb.tanggal BETWEEN '2026-01-01' AND '2026-08-30'
GROUP BY 
    kb.akun_cashbank_id, 
    kb.akun_kode_snapshot, 
    kb.akun_nama_snapshot, 
    kbd.kas_kategori_id, 
    kbd.pengeluaran_id, 
    kk.nama, 
    kp.pengeluaran_nama
ORDER BY kb.akun_kode_snapshot, total_nominal DESC;