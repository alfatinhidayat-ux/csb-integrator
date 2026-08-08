SELECT
    p.cabang_id,
    c.nama,
--     COUNT(*) AS dokumen,
    FORMAT(COALESCE(SUM(p.total_qty), 0), 0, 'id_ID') AS qty,
    CONCAT(
        'Rp ',
        FORMAT(COALESCE(SUM(p.total_nilai), 0), 0, 'id_ID')
    ) AS total_biaya,
    CONCAT(
        'Rp ',
        FORMAT(COALESCE(SUM(p.diskon_header), 0), 0, 'id_ID')
    ) AS diskon,
    CONCAT(
        'Rp ',
        FORMAT(COALESCE(SUM(p.grand_total), 0), 0, 'id_ID')
    ) AS net
FROM pembelian AS p
JOIN cabang AS c
    ON c.id = p.cabang_id
GROUP BY
    p.cabang_id,
    c.nama
ORDER BY
    c.id;