-- =====================================================================
-- PROGRES MIGRASI POS: brighter_pos (sumber/legacy) -> pos_transactions
--
-- Kunci migrasi: legacy_id + cabang_id (legacy_id = id di brighter_pos)
--
-- Pakai query tunggal yang menampilkan:
--   1. RINGKASAN TOTAL & PROGERS	(seluruh data)
--   2. PER CABANG
--   3. PER HARI (rentang valhaus, paling membantu pantau migrasi berjalan)
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. RINGKASAN TOTAL (satu baris)
-- ---------------------------------------------------------------------
-- pos_target_tanpa_legacy = data REAL aplikasi (bukan hasil migrasi),
-- tidak dihitung dalam progres migrasi.
SELECT
    (SELECT COUNT(*) FROM brighter_pos)                 AS pos_sumber_total,
    (SELECT COUNT(*) FROM pos_transactions
        WHERE legacy_id IS NOT NULL)                    AS pos_target_migrasi,
    (SELECT COUNT(*) FROM pos_transactions
        WHERE legacy_id IS NULL)                        AS pos_target_real_aplikasi,
    (SELECT COUNT(*) FROM pos_transactions)             AS pos_target_total,
    (SELECT COUNT(*) FROM pos_transactions
       JOIN brighter_pos b ON b.id = pos_transactions.legacy_id
        AND b.cabang_id = pos_transactions.cabang_id)   AS pos_target_matched,
    (SELECT COUNT(*) FROM pos_transactions
        JOIN brighter_pos  ON brighter_pos.id = pos_transactions.legacy_id
         AND brighter_pos.cabang_id = pos_transactions.cabang_id)
        / (SELECT COUNT(*) FROM brighter_pos) * 100     AS progres_persen;

-- ---------------------------------------------------------------------
-- 2. PER CABANG
-- ---------------------------------------------------------------------
SELECT
    b.cabang_id,
    COUNT(*)                  AS sum_total,
    SUM(CASE WHEN p.legacy_id IS NULL THEN 1 ELSE 0 END) AS belum_migrasi,
    ROUND(SUM(CASE WHEN p.legacy_id IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS progres_persen
FROM brighter_pos b
LEFT JOIN (
    SELECT DISTINCT legacy_id, cabang_id
    FROM pos_transactions
    WHERE legacy_id IS NOT NULL
) p ON p.legacy_id = b.id AND p.cabang_id = b.cabang_id
GROUP BY b.cabang_id
ORDER BY b.cabang_id;

-- ---------------------------------------------------------------------
-- 3. PER HARI (bantu pantau migrasi realtime)
-- ---------------------------------------------------------------------
SELECT
    b.tanggal AS tgl,
    COUNT(*) AS sum_s,
    SUM(CASE WHEN p.legacy_id IS NULL THEN 1 ELSE 0 END) AS belum,
    ROUND(SUM(CASE WHEN p.legacy_id IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS progres_persen
FROM brighter_pos b
LEFT JOIN (SELECT DISTINCT legacy_id, cabang_id FROM pos_transactions WHERE legacy_id IS NOT NULL) p
    ON p.legacy_id = b.id AND p.cabang_id = b.cabang_id
GROUP BY b.tanggal
ORDER BY b.tanggal
LIMIT 50;