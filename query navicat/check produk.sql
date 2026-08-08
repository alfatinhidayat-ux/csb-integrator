SELECT
    cabang_id,
    produk_id,
    COUNT(*) AS jumlah_konversi
FROM produk_satuan_konversi_cabang
GROUP BY cabang_id, produk_id
HAVING COUNT(*) > 1
ORDER BY cabang_id, produk_id;SELECT
    cabang_id,
    produk_id,
    COUNT(*) AS jumlah_konversi
FROM produk_satuan_konversi_cabang
GROUP BY cabang_id, produk_id
HAVING COUNT(*) > 1
ORDER BY cabang_id, produk_id;