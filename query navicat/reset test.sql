-- Disable foreign key checks sementara
SET FOREIGN_KEY_CHECKS = 0;

-- =============================================================================
-- 1. RESET TABEL DEPOSIT CUSTOMER & SALDO DEPOSIT
-- =============================================================================
-- Mengosongkan riwayat transaksi deposit & mereset auto-increment nomor bukti/id deposit
TRUNCATE TABLE customer_deposits;

-- Mereset saldo deposit seluruh customer menjadi Rp 0
UPDATE customer SET deposit_rp = 0;


-- =============================================================================
-- 2. RESET POS TRANSACTIONS (Kasir POS & Nomor Invoice)
-- =============================================================================
TRUNCATE TABLE pos_transaction_items;
TRUNCATE TABLE pos_transactions;
TRUNCATE TABLE piutang;
TRUNCATE TABLE pos_invoice_sequences;
TRUNCATE TABLE pos_invoice_reservations;

TRUNCATE TABLE media;


TRUNCATE TABLE retur_penjualan;
TRUNCATE TABLE retur_penjualan_detail;
TRUNCATE TABLE retur_penjualan_media;
TRUNCATE TABLE retur_penjualan;


-- =============================================================================
-- 3. RESET SALES ORDERS (Order Penjualan POS & Nomor SO)
-- =============================================================================
TRUNCATE TABLE pos_sales_order_items;
TRUNCATE TABLE pos_sales_orders;
TRUNCATE TABLE pos_sales_order_sequences;
TRUNCATE TABLE pos_sales_order_number_reservations;


-- =============================================================================
-- 4. RESET KAS & BANK (Kas Masuk, Kas Keluar, Lampiran, & Nomor Bukti)
-- =============================================================================
TRUNCATE TABLE kas_bank_media;
TRUNCATE TABLE kas_bank_detail;
TRUNCATE TABLE kas_bank;
TRUNCATE TABLE kas_bank_sequences;
TRUNCATE TABLE kas_masuk;


-- =============================================================================
-- 5. RESET SALDO KAS HARIAN (Sesi Kasir Harian)
-- =============================================================================
TRUNCATE TABLE saldo_kas_harian;


-- =============================================================================
-- 6. RESET PIUTANG & SURAT PENGIRIMAN (Opsional - Terkait POS/SO)
-- =============================================================================
TRUNCATE TABLE piutang_ledgers;
TRUNCATE TABLE piutang_payments;
TRUNCATE TABLE piutang;
TRUNCATE TABLE surat_pengiriman_detail_items;
TRUNCATE TABLE surat_pengiriman_detail;
TRUNCATE TABLE surat_pengiriman;
TRUNCATE TABLE surat_pengiriman_sequences;

TRUNCATE TABLE pos_sales_orders;
TRUNCATE TABLE pos_sales_order_sequences;
TRUNCATE TABLE pos_sales_order_number_reservations;
TRUNCATE TABLE pos_sales_order_items;
TRUNCATE TABLE pos_invoice_sequences;
TRUNCATE TABLE pos_invoice_reservations;
TRUNCATE TABLE pos_transactions;
TRUNCATE TABLE pos_transaction_items;
TRUNCATE TABLE pos_sales_orders;

TRUNCATE TABLE piutang_pelunasan;
TRUNCATE TABLE piutang_pelunasan_items;

-- Enable kembali foreign key checks
SET FOREIGN_KEY_CHECKS = 1;

SET FOREIGN_KEY_CHECKS = 0;
TRUNCATE TABLE customer_deposits;
UPDATE customer SET deposit_rp = 0;
TRUNCATE TABLE pos_transaction_items;
TRUNCATE TABLE pos_transactions;
TRUNCATE TABLE piutang;
TRUNCATE TABLE pos_invoice_sequences;
TRUNCATE TABLE pos_invoice_reservations;
SET FOREIGN_KEY_CHECKS = 1;