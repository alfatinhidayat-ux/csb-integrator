-- jika opsi hanya lunas semua baik lunas dan belum lunas
SELECT
    cabang_id,
--     COUNT(*) AS records,
--     COUNT(DISTINCT cust) AS pelanggan,
c.nama,

    CONCAT(
        'Rp ',
        FORMAT(COALESCE(SUM(total), 0), 0, 'id_ID')
    ) AS total_piutang,

    CONCAT(
        'Rp ',
        FORMAT(COALESCE(SUM(sisa), 0), 0, 'id_ID')
    ) AS sisa_piutang,

    SUM(
        cust_data_cust_nama IS NULL
        OR cust_data_cust_nama = ''
    ) AS tanpa_nama

FROM brighter_transaksi_piutang_customer_detail
JOIN cabang AS c
    ON c.id = brighter_transaksi_piutang_customer_detail.cabang_id
GROUP BY cabang_id
ORDER BY cabang_id;

-- jika opsi lunas hanya yang belum lunas saja 
-- SELECT d.cabang_id, c.nama,
--        COUNT(*) AS total_faktur,
--        CONCAT('Rp ', FORMAT(SUM(d.total), 0, 'id_ID')) AS total_piutang,
--        CONCAT('Rp ', FORMAT(SUM(d.sisa), 0, 'id_ID'))  AS sisa_piutang
-- FROM brighter_transaksi_piutang_customer_detail d
-- JOIN cabang c ON c.id = d.cabang_id
-- WHERE d.status = 'piutang'
-- GROUP BY d.cabang_id, c.nama ORDER BY d.cabang_id;


-- Tinggal filter status = 'lunas':
-- SELECT d.cabang_id, c.nama,
--        COUNT(*)                                                        AS total_faktur_lunas,
--        CONCAT('Rp ', FORMAT(SUM(d.total), 0, 'id_ID'))                 AS total_piutang,
--        CONCAT('Rp ', FORMAT(SUM(d.sisa), 0, 'id_ID'))                  AS sisa_piutang
-- FROM brighter_transaksi_piutang_customer_detail d
-- JOIN cabang c ON c.id = d.cabang_id
-- WHERE d.status = 'lunas'
-- GROUP BY d.cabang_id, c.nama
-- ORDER BY d.cabang_id;