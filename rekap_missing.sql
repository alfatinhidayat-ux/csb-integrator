WITH RECURSIVE date_range AS (
    SELECT MIN(DATE(tanggal)) AS tgl
    FROM brighter_pos

    UNION ALL

    SELECT DATE_ADD(tgl, INTERVAL 1 DAY)
    FROM date_range
    WHERE tgl < (
        SELECT MAX(DATE(tanggal))
        FROM brighter_pos
    )
),

cabang_list AS (
    SELECT
        cabang_id,
        MIN(DATE(tanggal)) AS tgl_awal,
        MAX(DATE(tanggal)) AS tgl_akhir
    FROM brighter_pos
    GROUP BY cabang_id
),

tanggal_ada AS (
    SELECT DISTINCT
        cabang_id,
        DATE(tanggal) AS tanggal
    FROM brighter_pos
),

tanggal_hilang AS (
    SELECT
        cl.cabang_id,
        dr.tgl AS tanggal_hilang
    FROM cabang_list cl
    JOIN date_range dr
        ON dr.tgl BETWEEN cl.tgl_awal AND cl.tgl_akhir
    LEFT JOIN tanggal_ada ta
        ON ta.cabang_id = cl.cabang_id
       AND ta.tanggal = dr.tgl
    WHERE ta.tanggal IS NULL
)

SELECT
    th.cabang_id,
    c.nama AS nama_cabang,
    MIN(th.tanggal_hilang) AS start_date,
    MAX(th.tanggal_hilang) AS end_date
FROM tanggal_hilang th
LEFT JOIN cabang c
    ON c.id = th.cabang_id
GROUP BY
    th.cabang_id,
    c.nama
ORDER BY
    th.cabang_id;
