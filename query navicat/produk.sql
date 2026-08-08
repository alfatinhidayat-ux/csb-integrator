SELECT
	pg.group_id,
	pg.group_kode,
	pg.group_nama,
	pgs.id AS sub_group_id,
	pgs.nama AS sub_group_nama,
	pgs.STATUS AS sub_group_status,
	pg.cabang_id 
FROM
	produk_group pg
	LEFT JOIN produk_group_sub pgs ON CAST( pgs.group_id AS UNSIGNED ) = pg.group_id 
	AND pgs.cabang_id = pg.cabang_id 
	AND pgs.STATUS = 'aktif' 
	AND pgs.deleted_at IS NULL 
WHERE
	pg.deleted_at IS NULL 
ORDER BY
	pg.group_nama ASC,
	pgs.nama ASC;