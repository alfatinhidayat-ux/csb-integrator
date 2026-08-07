-- =====================================================================
-- REKAP TANGGAL HILANG DI brighter_pos (per cabang)
-- v2: gap dikelompokkan per BLOK berurutan (bukan MIN-MAX satu rentang),
--     dan diberi label penyebab (Minggu / Idulfitri / Idul Adha / cabang baru).
--
-- CATATAN PENYEBAB (terkonfirmasi dari sheet Info Excel & pemilik):
--   * Idulfitri 2026 : Kairatu (5) 18-24 Mar; Kobisonta/Bula/Mandiri (1,2,4) 21-27 Mar
--   * Idul Adha      : 27 Mei 2026 (semua cabang tutup)
--   * Cabang Piru (7): baru beroperasi 15 Mei 2026 (April: 2 nota saja)
-- =====================================================================
WITH RECURSIVE date_range AS (
    SELECT MIN(DATE(tanggal)) AS tgl
    FROM brighter_pos
    UNION ALL
    SELECT DATE_ADD(tgl, INTERVAL 1 DAY)
    FROM date_range
    WHERE tgl < (SELECT MAX(DATE(tanggal)) FROM brighter_pos)
),
cabang_rentang AS (
    SELECT
        b.cabang_id,
        c.nama AS nama_cabang,
        MIN(DATE(b.tanggal)) AS tgl_awal,
        MAX(DATE(b.tanggal)) AS tgl_akhir
    FROM brighter_pos b
    LEFT JOIN cabang c ON c.id = b.cabang_id
    GROUP BY b.cabang_id, c.nama
),
tanggal_ada AS (
    SELECT DISTINCT cabang_id, DATE(tanggal) AS tanggal
    FROM brighter_pos
),
-- langkah 1: daftar tiap tanggal hilang + penyebabnya
hilang_bertanda AS (
    SELECT
        cl.cabang_id,
        cl.nama_cabang,
        dr.tgl AS tanggal,
        DAYNAME(dr.tgl) AS hari,
        CASE
            -- Piru baru beroperasi 15 Mei 2026 -> sebelum itu dianggap cabang baru
            WHEN cl.cabang_id = 7 AND dr.tgl < '2026-05-15' THEN 'Cabang baru (mulai operasi 15-05-2026)'
            -- Idulfitri Kairatu
            WHEN cl.cabang_id = 5 AND dr.tgl BETWEEN '2026-03-18' AND '2026-03-24' THEN 'LIBUR IDULFITRI'
            -- Idulfitri Kobisonta/Bula/Mandiri
            WHEN cl.cabang_id IN (1,2,4) AND dr.tgl BETWEEN '2026-03-21' AND '2026-03-27' THEN 'LIBUR IDULFITRI'
            -- Idul Adha 2026
            WHEN dr.tgl = '2026-05-27' THEN 'LIBUR IDUL ADHA 2026'
            -- hari Minggu
            WHEN WEEKDAY(dr.tgl) = 6 THEN 'Minggu (libur)'
            ELSE '*** PERLU CEK / DATA BOLONG ***'
        END AS alasan
    FROM cabang_rentang cl
    JOIN date_range dr ON dr.tgl BETWEEN cl.tgl_awal AND cl.tgl_akhir
    LEFT JOIN tanggal_ada ta
        ON ta.cabang_id = cl.cabang_id AND ta.tanggal = dr.tgl
    WHERE ta.tanggal IS NULL
),
-- langkah 2: beri nomor baris untuk deteksi blok berurutan
bernomor AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY cabang_id ORDER BY tanggal) AS rn
    FROM hilang_bertanda
),
-- langkah 3: hitung ukuran blok berurutan (gap detection)
blok AS (
    SELECT
        cabang_id,
        nama_cabang,
        tanggal,
        alasan,
        DATE_SUB(tanggal, INTERVAL rn DAY) AS grp_key,
        rn
    FROM bernomor
)
    SELECT
        cabang_id,
        nama_cabang,
        MIN(tanggal) AS start_date,
        MAX(tanggal) AS end_date,
        alasan,
        COUNT(*) AS jml_hari
    FROM blok
    GROUP BY cabang_id, nama_cabang, grp_key, alasan
    ORDER BY cabang_id, MIN(tanggal);
