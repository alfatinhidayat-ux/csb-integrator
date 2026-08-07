WITH RECURSIVE date_range AS (
    SELECT MIN(tanggal) AS tgl FROM brighter_pos
    UNION ALL
    SELECT DATE_ADD(tgl, INTERVAL 1 DAY)
    FROM date_range
    WHERE tgl < (SELECT MAX(tanggal) FROM brighter_pos)
),
cabang_list AS (
    SELECT DISTINCT cabang_id, MIN(tanggal) AS tgl_awal, MAX(tanggal) AS tgl_akhir
    FROM brighter_pos
    GROUP BY cabang_id
),
tanggal_ada AS (
    SELECT DISTINCT cabang_id, tanggal FROM brighter_pos
)
SELECT c.cabang_id, d.tgl AS tanggal_hilang
FROM cabang_list c
JOIN date_range d
    ON d.tgl BETWEEN c.tgl_awal AND c.tgl_akhir
LEFT JOIN tanggal_ada t
    ON t.cabang_id = c.cabang_id AND t.tanggal = d.tgl
WHERE t.tanggal IS NULL
ORDER BY c.cabang_id, d.tgl;
