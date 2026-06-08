from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Strategy(Enum):
    DELTA = "delta"
    FULL_PAGING = "full_paging"
    FULL_REPLACE = "full_replace"


@dataclass
class Endpoint:
    name: str
    path: str
    table: str
    strategy: Strategy
    cabang_param: Optional[str] = None
    params: dict = field(default_factory=dict)
    id_field: str = "id"
    response_root: str = "data"
    skip: bool = False


ENDPOINTS: list[Endpoint] = [
    # ── Auth ──────────────────────────────────────────────────────
    Endpoint("Me", "/me", "auth_me", Strategy.FULL_REPLACE, params={}),
    Endpoint("Env", "/env", "auth_env", Strategy.FULL_REPLACE, params={}),
    # ── Sistem - Users ────────────────────────────────────────────
    Endpoint(
        "Users",
        "/sistem/users/list",
        "sistem_users",
        Strategy.DELTA,
        cabang_param="user_cabang_id",
        params={"user_aktif": "Aktif", "data_group_detail": "true", "user_karyawan_data": "true"},
    ),
    Endpoint(
        "User by ID",
        "/sistem/users/:id",
        "sistem_users_detail",
        Strategy.FULL_REPLACE,
        params={},
    ),
    Endpoint(
        "User Cabang",
        "/sistem/users/:id/users_cabang",
        "sistem_users_cabang",
        Strategy.DELTA,
        params={"uc_cabang_data": "true"},
    ),
    # ── Sistem - Permissions & Groups ─────────────────────────────
    Endpoint(
        "User Groups",
        "/sistem/usergroups",
        "sistem_usergroups",
        Strategy.DELTA,
        params={"group_active": "Aktif"},
    ),
    Endpoint(
        "Permissions Menu",
        "/sistem/permissions/menus/1",
        "sistem_permissions_menus",
        Strategy.FULL_REPLACE,
        params={"full_permissions_data": "true", "mobile_menu": "true", "hirarki_menu": "true"},
    ),
    # ── Master - Produk ───────────────────────────────────────────
    Endpoint(
        "Produk",
        "/master/produk",
        "master_produk",
        Strategy.DELTA,
    ),
    Endpoint(
        "Produk by ID",
        "/master/produk/:produk_id",
        "master_produk_detail",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Satuan Konversi Produk",
        "/master/produk/:produk_id/satuan_konversi",
        "master_produk_satuan_konversi",
        Strategy.FULL_PAGING,
        params={
            "konversi_aktif": "Aktif",
            "konversi_satuan_data": "true",
            "konversi_stok_data": "true",
        },
    ),
    Endpoint(
        "Satuan Konversi Cabang Produk",
        "/master/produk/:produk_id/satuan_konversi_cabang",
        "master_produk_satuan_konversi_cabang",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Foto Produk",
        "/master/produk/:produk_id/pfoto",
        "master_produk_foto",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Produk Brand",
        "/master/produk_brand",
        "master_produk_brand",
        Strategy.DELTA,
    ),
    Endpoint(
        "Produk Group",
        "/master/produk_group",
        "master_produk_group",
        Strategy.DELTA,
        params={"group_aktif": "Aktif"},
    ),
    Endpoint(
        "Produk Group Sub",
        "/master/produk_group_sub",
        "master_produk_group_sub",
        Strategy.DELTA,
        params={"group_data": "true"},
    ),
    # ── Master - Cabang & Supplier ────────────────────────────────
    Endpoint(
        "Cabang",
        "/master/cabang",
        "master_cabang",
        Strategy.FULL_PAGING,
        params={"cabang_aktif": "Aktif"},
    ),
    Endpoint(
        "Supplier",
        "/master/supplier",
        "master_supplier",
        Strategy.DELTA,
        params={"supplier_aktif": "Aktif"},
    ),
    Endpoint(
        "Customer",
        "/master/customer",
        "master_customer",
        Strategy.DELTA,
        cabang_param="cust_cabang_id",
        params={"cust_aktif": "Aktif"},
    ),
    Endpoint(
        "Customer by ID",
        "/master/customer/:id",
        "master_customer_detail",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Kategori Customer",
        "/master/kategori_customer",
        "master_kategori_customer",
        Strategy.FULL_PAGING,
        params={"ckat_aktif": "Aktif"},
    ),
    Endpoint(
        "Deposit Customer",
        "/master/deposit",
        "master_deposit",
        Strategy.DELTA,
        cabang_param="deposit_cabang_id",
        params={"deposit_customer_data": "true", "deposit_status_dokumen": "Disetujui"},
    ),
    # ── Master - Karyawan ─────────────────────────────────────────
    Endpoint(
        "Karyawan",
        "/master/karyawan",
        "master_karyawan",
        Strategy.DELTA,
        cabang_param="karyawan_cabang_id",
        params={
            "karyawan_aktif": "Aktif",
            "karyawan_departemen_data": "true",
            "karyawan_jabatan_data": "true",
        },
    ),
    Endpoint(
        "Karyawan by ID",
        "/master/karyawan/:id",
        "master_karyawan_detail",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Karyawan Gaji",
        "/master/karyawan/:id/karyawan_gaji",
        "master_karyawan_gaji",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Departemen",
        "/master/departement",
        "master_departemen",
        Strategy.DELTA,
        params={"departemen_aktif": "Aktif"},
    ),
    Endpoint(
        "Jabatan",
        "/master/jabatan",
        "master_jabatan",
        Strategy.FULL_REPLACE,
    ),
    # ── Master - Keuangan ─────────────────────────────────────────
    Endpoint(
        "Akun",
        "/master/akun",
        "master_akun",
        Strategy.DELTA,
        params={"akun_aktif": "Aktif", "akun_group": "Semua"},
    ),
    Endpoint(
        "Penerimaan",
        "/master/keuangan/penerimaan",
        "master_keuangan_penerimaan",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Pengeluaran",
        "/master/keuangan/pengeluaran",
        "master_keuangan_pengeluaran",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Akun CashBank",
        "/master/akun",
        "master_akun_cashbank",
        Strategy.DELTA,
        params={"akun_aktif": "Aktif", "akun_group": "CashBank"},
    ),
    Endpoint(
        "Akun Pengeluaran Lain",
        "/master/akun",
        "master_akun_pengeluaran_lain",
        Strategy.DELTA,
        params={"akun_aktif": "Aktif", "akun_group": "PengeluaranLain"},
    ),
    # ── Master - Logistik ─────────────────────────────────────────
    Endpoint(
        "Kapal",
        "/master/kapal",
        "master_kapal",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Kapal by ID",
        "/master/kapal/:id",
        "master_kapal_detail",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Pelabuhan",
        "/master/pelabuhan",
        "master_pelabuhan",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Container",
        "/master/container",
        "master_container",
        Strategy.FULL_REPLACE,
    ),
    # ── Persediaan - Surat Permintaan ─────────────────────────────
    Endpoint(
        "Surat Permintaan",
        "/persediaan/surat_permintaan",
        "persediaan_surat_permintaan",
        Strategy.DELTA,
        cabang_param="omutasi_cabang_id",
        params={
            "omutasi_status": "Semua",
            "omutasi_order_detail_produk_data": "false",
            "omutasi_pemohon_data": "true",
            "omutasi_cabang_data": "true",
            "omutasi_tujuan_cabang_data": "true",
        },
    ),
    # ── Persediaan - Surat Jalan ──────────────────────────────────
    Endpoint(
        "Surat Jalan",
        "/persediaan/surat_jalan",
        "persediaan_surat_jalan",
        Strategy.DELTA,
        cabang_param="sj_cabang_id",
        params={
            "sj_status_dok": "Semua",
            "sj_kapal_data": "true",
            "sj_truck_data": "true",
        },
    ),
    Endpoint(
        "Surat Jalan Produk",
        "/persediaan/surat_jalan/:id/surat_jalan_produk",
        "persediaan_surat_jalan_produk",
        Strategy.DELTA,
        params={"sjp_produk_data": "true", "sjp_satuan_data": "true"},
    ),
    Endpoint(
        "Surat Jalan File",
        "/persediaan/surat_jalan/:id/sj_file",
        "persediaan_surat_jalan_file",
        Strategy.FULL_REPLACE,
    ),
    # ── Persediaan - Mutasi Barang ────────────────────────────────
    Endpoint(
        "Mutasi Barang Keluar",
        "/persediaan/mutasi_barang",
        "persediaan_mutasi_barang",
        Strategy.DELTA,
        cabang_param="mutasi_cabang_barang_keluar",
        params={"mutasi_status": "Semua", "mutasi_pemohon_data": "true"},
    ),
    Endpoint(
        "Mutasi Barang by ID",
        "/persediaan/mutasi_barang/:id",
        "persediaan_mutasi_barang_detail",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Detail Mutasi Barang",
        "/persediaan/mutasi_barang/:id/detail_mutasi_barang",
        "persediaan_mutasi_barang_item",
        Strategy.FULL_PAGING,
        params={
            "dmutasi_produk_data": "true",
            "dmutasi_satuan_data": "true",
            "dmutasi_produk_satuan_konversi": "true",
        },
    ),
    # ── Persediaan - Pembelian ────────────────────────────────────
    Endpoint(
        "Order Pembelian",
        "/persediaan/order_pembelian",
        "persediaan_order_pembelian",
        Strategy.DELTA,
        cabang_param="order_cabang_id",
        params={
            "order_status": "Semua",
            "order_status_menyetujui": "Semua",
            "order_supplier_data": "true",
        },
    ),
    Endpoint(
        "Pembelian Belum Lunas",
        "/persediaan/pembelian",
        "persediaan_pembelian",
        Strategy.DELTA,
        cabang_param="pembelian_cabang_id",
        params={
            "pembelian_status_dok": "Tertutup",
            "pembelian_status_lunas": "Belum Lunas",
            "pembelian_supplier_data": "true",
        },
    ),
    Endpoint(
        "Supplier Punya Hutang",
        "/persediaan/pembelian/supplier/list_hutang",
        "persediaan_supplier_hutang",
        Strategy.DELTA,
        cabang_param="pembelian_cabang_id",
        params={"supplier_aktif": "Aktif"},
    ),
    Endpoint(
        "Permintaan Pembelian Diorder",
        "/persediaan/permintaan_pembelian/detail_produk/diorder",
        "persediaan_permintaan_pembelian_diorder",
        Strategy.DELTA,
        params={"permintaan_pembelian_cabang_data": "true"},
    ),
    Endpoint(
        "Retur Pembelian",
        "/persediaan/retur_pembelian",
        "persediaan_retur_pembelian",
        Strategy.DELTA,
        cabang_param="rpembelian_cabang_id",
        params={
            "rpembelian_stat_dok": "Semua",
            "rpembelian_supplier_data": "true",
            "rpembelian_cabang_data": "true",
            "rpembelian_faktur_data": "true",
        },
    ),
    Endpoint(
        "Retur Pembelian by ID",
        "/persediaan/retur_pembelian/:id",
        "persediaan_retur_pembelian_detail",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Detail Retur Pembelian",
        "/persediaan/retur_pembelian/:id/rpembelian_det",
        "persediaan_retur_pembelian_item",
        Strategy.DELTA,
        params={"rpembelian_det_produk_data": "true", "rpembelian_det_satuan_data": "true"},
    ),
    # ── Persediaan - Penyesuaian Stok ─────────────────────────────
    Endpoint(
        "Penyesuaian Stok",
        "/persediaan/penyesuaian_stok",
        "persediaan_penyesuaian_stok",
        Strategy.DELTA,
        cabang_param="koreksi_cabang_id",
        params={"koreksi_status": "Semua"},
    ),
    Endpoint(
        "Penyesuaian Stok by ID",
        "/persediaan/penyesuaian_stok/:id",
        "persediaan_penyesuaian_stok_detail",
        Strategy.DELTA,
    ),
    Endpoint(
        "Detail Penyesuaian Stok",
        "/persediaan/penyesuaian_stok/:id/detail_penyesuaian_stok",
        "persediaan_penyesuaian_stok_item",
        Strategy.FULL_PAGING,
        params={"dkoreksi_produk_data": "true", "dkoreksi_satuan_data": "true"},
    ),
    # ── Transaksi - Penjualan ─────────────────────────────────────
    Endpoint(
        "Order Jual",
        "/transaksi/order_jual",
        "transaksi_order_jual",
        Strategy.DELTA,
        cabang_param="ojual_cabang_id",
        params={
            "ojual_stat_dok": "Semua",
            "ojual_gudang_data": "true",
            "ojual_cust_data": "true",
            "ojual_jual_produk_data": "true",
            "ojual_detail_file_data": "true",
        },
    ),
    Endpoint(
        "Order Jual by ID",
        "/transaksi/order_jual/:id",
        "transaksi_order_jual_detail",
        Strategy.DELTA,
        params={
            "ojual_gudang_data": "true",
            "ojual_cust_data": "true",
            "ojual_jual_produk_data": "true",
        },
    ),
    Endpoint(
        "Detail Produk Order Jual",
        "/transaksi/order_jual/:id/detail_ojual_produk",
        "transaksi_order_jual_produk",
        Strategy.FULL_REPLACE,
        params={"dojual_produk_produk_data": "true", "dojual_produk_satuan_data": "true"},
    ),
    Endpoint(
        "Surat Kirim Jual",
        "/transaksi/surat_kirim_jual",
        "transaksi_surat_kirim_jual",
        Strategy.DELTA,
        cabang_param="skj_cabang_id",
        params={
            "skj_stat_dok": "Semua",
            "skj_cust_data": "true",
            "skj_order_jual_data": "true",
        },
    ),
    Endpoint(
        "Surat Kirim Jual by ID",
        "/transaksi/surat_kirim_jual/:id",
        "transaksi_surat_kirim_jual_detail",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Detail Produk Surat Kirim",
        "/transaksi/surat_kirim_jual/:id/detail_skj_produk",
        "transaksi_surat_kirim_jual_produk",
        Strategy.DELTA,
        params={"dskj_produk_data": "true", "dskj_satuan_data": "true"},
    ),
    Endpoint(
        "Surat Kirim Jual File",
        "/transaksi/surat_kirim_jual/:id/skj_file",
        "transaksi_surat_kirim_jual_file",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Retur Penjualan",
        "/transaksi/retur_penjualan",
        "transaksi_retur_penjualan",
        Strategy.DELTA,
        cabang_param="retur_cabang_id",
        params={
            "retur_stat_dok": "Semua",
            "retur_cust_data": "true",
            "retur_skj_data": "true",
            "retur_order_jual_data": "true",
            "retur_penerimaan_data": "true",
        },
    ),
    Endpoint(
        "Retur Penjualan by ID",
        "/transaksi/retur_penjualan/:id",
        "transaksi_retur_penjualan_detail",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Detail Retur Penjualan",
        "/transaksi/retur_penjualan/:id/rjproduk_det",
        "transaksi_retur_penjualan_produk",
        Strategy.FULL_PAGING,
        params={"rjproduk_produk_data": "true", "rjproduk_satuan_data": "true"},
    ),
    # ── Transaksi - Keuangan (Hutang & Piutang) ───────────────────
    Endpoint(
        "Hutang Pembelian",
        "/transaksi/hutang_pembelian",
        "transaksi_hutang_pembelian",
        Strategy.DELTA,
        cabang_param="hp_cabang_id",
        params={"hp_supplier_data": "true"},
    ),
    Endpoint(
        "Piutang Penjualan",
        "/transaksi/piutang_penjualan",
        "transaksi_piutang_penjualan",
        Strategy.DELTA,
        cabang_param="piutang_cabang_id",
        params={"piutang_cust_data": "true"},
    ),
    Endpoint(
        "Riwayat Bayar Piutang",
        "/transaksi/piutang_penjualan/customer/list_piutang/:cust_id/detail",
        "transaksi_piutang_penjualan_riwayat",
        Strategy.FULL_PAGING,
    ),
    Endpoint(
        "Pelunasan Hutang",
        "/transaksi/pelunasan_hutang",
        "transaksi_pelunasan_hutang",
        Strategy.DELTA,
        cabang_param="lunas_hutang_cabang_id",
        params={"lunas_hutang_supplier_data": "true"},
    ),
    Endpoint(
        "Pelunasan Hutang by ID",
        "/transaksi/pelunasan_hutang/:id",
        "transaksi_pelunasan_hutang_detail",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Detail Pelunasan Hutang",
        "/transaksi/pelunasan_hutang/:id/dfhutang",
        "transaksi_pelunasan_hutang_item",
        Strategy.DELTA,
        params={"dfhutang_produk_data": "true"},
    ),
    Endpoint(
        "Foto Bukti Pelunasan",
        "/transaksi/pelunasan_hutang/:id/dfhutang_foto",
        "transaksi_pelunasan_hutang_foto",
        Strategy.FULL_REPLACE,
    ),
    # ── Transaksi - Kas Harian ────────────────────────────────────
    Endpoint(
        "Kas Harian Penerimaan",
        "/transaksi/kas_harian/penerimaan",
        "transaksi_kas_harian_penerimaan",
        Strategy.DELTA,
        params={"kas_harian_cabang_data": "true"},
    ),
    Endpoint(
        "Kas Harian Pengeluaran",
        "/transaksi/kas_harian/pengeluaran",
        "transaksi_kas_harian_pengeluaran",
        Strategy.DELTA,
        params={"kas_harian_cabang_data": "true"},
    ),
    Endpoint(
        "Rekap Penerimaan",
        "/transaksi/kas_harian/penerimaan/rekap",
        "transaksi_kas_harian_penerimaan_rekap",
        Strategy.FULL_REPLACE,
    ),
    # ── Persediaan - Konosemen & Barang Titipan ───────────────────
    Endpoint(
        "Konosemen",
        "/persediaan/konosemen",
        "persediaan_konosemen",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Barang Titipan Internal",
        "/persediaan/barang_titipan_internal",
        "persediaan_barang_titipan_internal",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Detail Barang Titipan Internal",
        "/persediaan/barang_titipan_internal/:id/detail_barang_titipan_internal",
        "persediaan_barang_titipan_internal_item",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Barang Titipan Eksternal",
        "/persediaan/barang_titipan_eksternal",
        "persediaan_barang_titipan_eksternal",
        Strategy.FULL_REPLACE,
    ),
    # ── Personalia ────────────────────────────────────────────────
    Endpoint(
        "Pengajuan Pinjaman Karyawan",
        "/personalia/pengajuan_pinjaman_karyawan",
        "personalia_pinjaman_karyawan",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Pinjaman Karyawan by ID",
        "/personalia/pengajuan_pinjaman_karyawan/:id",
        "personalia_pinjaman_karyawan_detail",
        Strategy.FULL_REPLACE,
    ),
    # ── Administrasi ──────────────────────────────────────────────
    Endpoint(
        "Info Perusahaan",
        "/administrasi/info_perusahaan",
        "administrasi_info_perusahaan",
        Strategy.DELTA,
    ),
    # ── Laporan - Dashboard ───────────────────────────────────────
    Endpoint(
        "Rekap Dashboard",
        "/laporan/dashboard/rekap_dashboard",
        "laporan_dashboard_rekap",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Penerimaan Per User",
        "/laporan/dashboard/penerimaan_per_user",
        "laporan_dashboard_penerimaan_user",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Saldo Rekapitulasi",
        "/transaksi/kas_harian/rekapitulasi/saldo",
        "laporan_rekapitulasi_saldo",
        Strategy.FULL_REPLACE,
    ),
    # ── Akuntansi - Kas/Bank Masuk ────────────────────────────────
    Endpoint(
        "Kas Bank Masuk",
        "/akuntansi/kasbank_masuk",
        "akuntansi_kasbank_masuk",
        Strategy.DELTA,
        params={"kasbank_masuk_cabang_data": "true"},
    ),
    Endpoint(
        "Kas Bank Masuk by ID",
        "/akuntansi/kasbank_masuk/:id",
        "akuntansi_kasbank_masuk_detail",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Detail Kas Bank Masuk",
        "/akuntansi/kasbank_masuk/:id/detail_kasbank_masuk",
        "akuntansi_kasbank_masuk_item",
        Strategy.DELTA,
    ),
    Endpoint(
        "Detail Penerimaan Lain",
        "/akuntansi/kasbank_masuk/:id/detail_penerimaan_lain",
        "akuntansi_kasbank_masuk_penerimaan_lain",
        Strategy.FULL_PAGING,
    ),
    Endpoint(
        "Detail Piutang Karyawan",
        "/akuntansi/kasbank_masuk/:id/detail_piutang_karyawan",
        "akuntansi_kasbank_masuk_piutang_karyawan",
        Strategy.DELTA,
    ),
    Endpoint(
        "Bukti Pelunasan",
        "/akuntansi/kasbank_masuk/:id/bukti_pelunasan",
        "akuntansi_kasbank_masuk_bukti_pelunasan",
        Strategy.FULL_REPLACE,
    ),
    # ── Akuntansi - Kas/Bank Keluar ───────────────────────────────
    Endpoint(
        "Kas Bank Keluar",
        "/akuntansi/kasbank_keluar",
        "akuntansi_kasbank_keluar",
        Strategy.DELTA,
        params={"kasbank_keluar_cabang_data": "true"},
    ),
    Endpoint(
        "Kas Bank Keluar by ID",
        "/akuntansi/kasbank_keluar/:id",
        "akuntansi_kasbank_keluar_detail",
        Strategy.FULL_REPLACE,
    ),
    Endpoint(
        "Detail Kas Bank Keluar",
        "/akuntansi/kasbank_keluar/:id/detail_kasbank_keluar",
        "akuntansi_kasbank_keluar_item",
        Strategy.FULL_PAGING,
    ),
    Endpoint(
        "Detail Pengeluaran Lain",
        "/akuntansi/kasbank_keluar/:id/detail_pengeluaran_lain",
        "akuntansi_kasbank_keluar_pengeluaran_lain",
        Strategy.FULL_PAGING,
    ),
    # ── Akuntansi - Jurnal Umum ───────────────────────────────────
    Endpoint(
        "Jurnal Umum",
        "/akuntansi/jurnal_umum",
        "akuntansi_jurnal_umum",
        Strategy.DELTA,
    ),
    # ── Skip (PDF/print) ──────────────────────────────────────────
    Endpoint("Lap Penjualan Print", "/laporan/lap_penjualan/rekap/print", "", Strategy.FULL_REPLACE, skip=True),
    Endpoint("Lap Hutang Pembelian Print", "/laporan/lap_hutang_pembelian/print", "", Strategy.FULL_REPLACE, skip=True),
    Endpoint("Lap Pinjaman Karyawan Print", "/laporan/lap_pinjaman_karyawan/print", "", Strategy.FULL_REPLACE, skip=True),
    Endpoint("Lap Bukti Transfer Print", "/laporan/lap_bukti_transfer/print", "", Strategy.FULL_REPLACE, skip=True),
    Endpoint("Lap Kasbank Masuk Print", "/laporan/lap_kasbank_masuk/print", "", Strategy.FULL_REPLACE, skip=True),
    Endpoint("Lap Kasbank Keluar Print", "/laporan/lap_kasbank_keluar/print", "", Strategy.FULL_REPLACE, skip=True),
    Endpoint("Static File", "/static/:modul/:bulan/:nama_file", "", Strategy.FULL_REPLACE, skip=True),
]


SYNCABLE_ENDPOINTS = [ep for ep in ENDPOINTS if not ep.skip]
SKIPPED_ENDPOINTS = [ep for ep in ENDPOINTS if ep.skip]
