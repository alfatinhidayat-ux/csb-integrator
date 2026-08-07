-- ============================================================
-- Cek hasil migrasi pembelian -> csb_db.pembelian / pembelian_detail
-- Verifikasi terhadap PDF: mandiri(4), bula(2), piru(7)
-- ============================================================

-- 1. Rekon GRAND TOTAL per cabang (bandingkan dgn PDF)
SELECT cabang_id,
       COUNT(*) AS dokumen,
       SUM(total_qty) AS qty,
       SUM(total_nilai) AS total_biaya,
       SUM(diskon_header) AS diskon,
       SUM(grand_total) AS net
FROM pembelian
WHERE cabang_id IN (2,4,7)
GROUP BY cabang_id
ORDER BY cabang_id;

-- 2. 4 dok outlier (net != bayar+sisa sumber) di tabel hasil migrasi
SELECT p.id, p.kode, p.cabang_id, p.total_nilai, p.diskon_header, p.grand_total
FROM pembelian p
WHERE p.kode IN ('CSB/PL/2606-0018','CSB/PL/2607-0002','PRU/PL/2605-0220','PRU/PL/2605-0221');

-- 3. Konsistensi internal: gross - diskon harus = net (harus 0 baris)
SELECT COUNT(*) AS tidak_konsisten
FROM pembelian
WHERE ROUND(total_nilai - diskon_header, 2) <> ROUND(grand_total, 2);

-- 4. Detail vs header (harus cocok per dokumen)
SELECT p.id, p.kode, p.cabang_id, p.grand_total,
       ROUND(SUM(d.subtotal),2) AS detail_sum
FROM pembelian p
JOIN pembelian_detail d ON d.pembelian_id = p.id
WHERE p.cabang_id IN (2,4,7)
GROUP BY p.id, p.kode, p.cabang_id, p.grand_total
HAVING ROUND(SUM(d.subtotal),2) <> ROUND(p.grand_total,2);
