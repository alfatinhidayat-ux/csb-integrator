import dataclasses

from endpoints import ENDPOINTS

_BY_NAME = {ep.name: ep for ep in ENDPOINTS}


def find_endpoint(name: str):
    return _BY_NAME[name]


# Base "Produk" endpoint (shared with the brighter_mirror pipeline in endpoints.py)
# filters to produk_aktif=Aktif only. For csb_db we want the full catalog
# (12.539 produk per API, vs 9.697 if filtered to active) so inactive products
# are represented too - their status is tracked via produk_aktif/is_active,
# which are already in the update whitelist below. dataclasses.replace() makes
# a copy so endpoints.py / the brighter_mirror pipeline is untouched.
_produk_all = dataclasses.replace(
    find_endpoint("Produk"),
    params={"produk_brand_data": "true"},
)


# Columns considered "owned by Brighter" (catalog/reference data) and therefore
# safe to overwrite on every sync. Everything else on an existing row is left
# untouched - it's assumed to be maintained manually inside the CSB app.
#
# DRAFT - review with the CSB app team before running against production.
# See Open Questions in the sync plan.
PRODUK_UPDATE_COLS = [
    "produk_kode", "produk_nama", "produk_sku",
    "produk_group", "produk_group_sub", "produk_brand",
    "produk_satuan", "produk_satuan_default",
    "produk_satuan_nama", "produk_satuan_default_nama",
    "produk_berat", "produk_keterangan",
    "produk_aktif", "is_active", "produk_status",
    "produk_group_data", "produk_brand_data",
    "produk_satuan_konversi_data", "produk_satuan_konversi_cabang_data",
    "produk_foto_data", "has_foto",
    "produk_harga_beli_terakhir", "produk_diskon_beli_terakhir",
    "produk_satuan_beli_terakhir", "produk_satuan_beli_terakhir_kode",
    "produk_satuan_beli_terakhir_nama",
    "produk_barcode_url", "timestamp_data", "produk_id_parent",
]

PRODUK_BRAND_UPDATE_COLS = [
    "pbrand_kode", "pbrand_nama", "pbrand_keterangan", "pbrand_kelompok",
    "pbrand_aktif", "timestamp_data",
]

PRODUK_GROUP_UPDATE_COLS = [
    "group_kode", "group_nama", "group_aktif", "group_keterangan", "timestamp_data",
]

PRODUK_FOTO_UPDATE_COLS = [
    "pfoto_path", "pfoto_keterangan", "pfoto_default", "pfoto_url", "pfoto_size",
    "pfoto_url_thumbnail", "pfoto_size_thumbnail", "pfoto_url_medium", "pfoto_size_medium",
]

PRODUK_SATUAN_KONVERSI_UPDATE_COLS = [
    "konversi_produk", "konversi_satuan", "konversi_sku", "konversi_nilai",
    "konversi_harga", "konversi_panjang", "konversi_lebar", "konversi_tinggi",
    "konversi_volume", "konversi_berat", "konversi_aktif", "konversi_default",
    "konversi_keterangan", "konversi_satuan_data",
    "satuan_id", "satuan_kode", "satuan_nama", "satuan_aktif",
    "satuan_keterangan", "satuan_kemasan",
]

# Phase 1 resources - single natural-key tables only.
# "parent" resources are keyed off produk_id from the `produk` table and require
# per-product API calls (path template like /master/produk/:produk_id/pfoto).
RESOURCES = [
    dict(
        key="produk",
        endpoint=_produk_all,
        table="produk",
        pk="produk_id",
        update_cols=PRODUK_UPDATE_COLS,
        parent=None,
    ),
    dict(
        key="produk_brand",
        endpoint=find_endpoint("Produk Brand"),
        table="produk_brand",
        pk="pbrand_id",
        update_cols=PRODUK_BRAND_UPDATE_COLS,
        parent=None,
    ),
    dict(
        key="produk_group",
        endpoint=find_endpoint("Produk Group"),
        table="produk_group",
        pk="group_id",
        update_cols=PRODUK_GROUP_UPDATE_COLS,
        parent=None,
    ),
    dict(
        key="produk_foto",
        endpoint=find_endpoint("Foto Produk"),
        table="produk_foto",
        pk="pfoto_id",
        update_cols=PRODUK_FOTO_UPDATE_COLS,
        parent="produk",
    ),
    dict(
        key="produk_satuan_konversi",
        endpoint=find_endpoint("Satuan Konversi Produk"),
        table="produk_satuan_konversi",
        pk="konversi_id",
        update_cols=PRODUK_SATUAN_KONVERSI_UPDATE_COLS,
        parent="produk",
    ),
]
