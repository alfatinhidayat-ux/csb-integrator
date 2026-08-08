SELECT 
    me.id,
    me.uuid,
    CONCAT(me.nama_edc, ' - ', b.nama) AS label
FROM master_edc me
JOIN rekening r ON r.id = me.rekening_id
JOIN bank b     ON b.id = r.bank_id
WHERE me.aktif = 1
  AND me.deleted_at IS NULL
ORDER BY b.nama, me.nama_edc;