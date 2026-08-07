-- ============================================================
-- Cek hasil migrasi pembelian -> csb_db.pembelian / pembelian_detail
-- Verifikasi terhadap PDF: mandiri(4), bula(2), piru(7)
-- ============================================================

-- 1. Rekon GRAND TOTAL per cabang (bandingkan dgn PDF)
SELECT
    p.cabang_id,
    c.nama,
    COUNT(*) AS dokumen,
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
    c.nama;

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
