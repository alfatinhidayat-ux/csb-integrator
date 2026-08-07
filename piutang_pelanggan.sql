Berikut query rekon piutang per cabang (kolom mirror brighter_transaksi_piutang_customer_detail):
1. Rekap total & sisa per cabang:
SELECT cabang_id,
       COUNT(*)                               AS records,
       COUNT(DISTINCT cust)                   AS pelanggan,
       COALESCE(SUM(total), 0)                AS total_piutang,
       COALESCE(SUM(sisa), 0)                 AS sisa_piutang,
       SUM(cust_data_cust_nama IS NULL OR cust_data_cust_nama = '') AS tanpa_nama
FROM brighter_transaksi_piutang_customer_detail
GROUP BY cabang_id
ORDER BY cabang_id;
2. Rekap global (semua cabang):
SELECT COUNT(*)                 AS records,
       COUNT(DISTINCT cust)     AS customers,
       COALESCE(SUM(total), 0)  AS total_piutang,
       COALESCE(SUM(sisa), 0)   AS sisa_piutang
FROM brighter_transaksi_piutang_customer_detail;
3. Rinci per pelanggan (untuk dibandingkan dgn baris PDF):
SELECT cabang_id, cust, cust_data_cust_nama,
       COUNT(*)           AS jml_faktur,
       SUM(total)         AS total_piutang,
       SUM(sisa)          AS sisa_piutang
FROM brighter_transaksi_piutang_customer_detail
WHERE cabang_id = 4            -- ganti sesuai cabang
GROUP BY cabang_id, cust, cust_data_cust_nama
ORDER BY total_piutang DESC;
4. Cek faktur yang total/sisa-nya berubah atau duplikat (untuk deteksi anomali):
SELECT faktur, cabang_id, COUNT(*) jml
FROM brighter_transaksi_piutang_customer_detail
GROUP BY faktur, cabang_id
HAVING COUNT(*) > 1;
Catatan: DB ini sudah berisi filter faktur >= '2024-01-01'. Kalau mau memisahkan manapun juga (mis. sebelum 2024), tambahkan WHERE faktur_tanggal >= '2024-01-01' pada query di atas.
Mau query versi mana yang kamu pakai — per pelanggan untuk dibandingkan dengan laporan, atau cuma rekap total?
▣  Build · DeepSeek V4 Flash Free · 13.7s
