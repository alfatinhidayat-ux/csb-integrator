SELECT
    p.cabang_id,
		c.nama,
--     COUNT(DISTINCT p.id) AS jumlah_faktur,
	
    CONCAT(
        'Rp ',
        FORMAT(SUM(g.gross), 0, 'id_ID')
    ) AS hutang_awal,

    CONCAT(
        'Rp ',
        FORMAT(
            SUM(p.total_bayar_rp) + SUM(g.gross - p.total_net_rp),
            0,
            'id_ID'
        )
    ) AS terbayar,

    CONCAT(
        'Rp ',
        FORMAT(SUM(p.total_sisa_rp), 0, 'id_ID')
    ) AS sisa

FROM brighter_persediaan_pembelian p
JOIN cabang AS c
    ON c.id = p.cabang_id
JOIN (
    SELECT
        pembelian_det_master_id,
        SUM(pembelian_det_subtotal_rp) AS gross
    FROM brighter_persediaan_pembelian_detail
    GROUP BY pembelian_det_master_id
) g
    ON g.pembelian_det_master_id = p.id

WHERE p.status_dok = 'Tertutup'
GROUP BY p.cabang_id
ORDER BY p.cabang_id;