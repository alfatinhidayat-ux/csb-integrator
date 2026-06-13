Token  :

authorization
Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJyaW9uIiwidXNlcl9pZCI6MiwidXNlcl9uYW1lIjoicmlvbiIsImdydXAiOiJEaXJla3R1ciIsInNlc3Npb25faWQiOjI0MjIsImV4cCI6MTc4MzkxMTQxNX0._dj-jDezVtIsi-Y0dOnUTHzEisl9A8SGf1W2B7FoZpc



Request URL
https://brighter-kairatu-api.koffiesoft.com/laporan/lap_kartu_stok/rekap_produk?tanggal_awal=2026-06-01&tanggal_akhir=2026-06-13&order_by=produk_nama&order_dir=asc&opsi_satuan=default&cabang_ids=2%2C1%2C4%2C6%2C7%2C5&tanggal_awal=2026-06-01&tanggal_akhir=2026-06-13&order_by=produk_nama&order_dir=asc&opsi_satuan=default&cabang_ids=2&cabang_ids=1&cabang_ids=4&cabang_ids=6&cabang_ids=7&cabang_ids=5
Request Method
GET
Status Code
200 OK
Remote Address
103.167.136.25:443
Referrer Policy
strict-origin-when-cross-origin


jalankan request itu response nya masukan ketable ya itu untuk stock  dan nilai persediaan hpp


kira kira nanti response nya gini,
{
    "status": {
        "code": 200,
        "message": "Data Berhasil Ditampilkan."
    },
    "data": [
        {
            "produk_id": 7979,
            "produk_kode": "OP038",
            "produk_sku": "2120000038",
            "produk_nama": "2IN1 OLIMPIC THERA/PROCELLA PLATINUM 120X200",
            "satuan_id": 1,
            "satuan_nama": "PIECES",
            "satuan_kode": "PCS",
            "hpp_nilai_satuan": 0.0,
            "produk_harga_beli_terakhir": 2700000.0,
            "cabang_id_1": 1,
            "cabang_kode_1": "SB",
            "cabang_nama_1": "CSB - Kobisonta",
            "stok_awal_1": 0.0,
            "stok_masuk_1": 0.0,
            "stok_keluar_1": 0.0,
            "stok_akhir_1": 0.0,
            "cabang_id_2": 2,
            "cabang_kode_2": "CSB",
            "cabang_nama_2": "CSB - Bula",
            "stok_awal_2": 0.0,
            "stok_masuk_2": 0.0,
            "stok_keluar_2": 0.0,
            "stok_akhir_2": 0.0,
            "cabang_id_3": 4,
            "cabang_kode_3": "MDR",
            "cabang_nama_3": "CSB - Mandiri",
            "stok_awal_3": 0.0,
            "stok_masuk_3": 0.0,
            "stok_keluar_3": 0.0,
            "stok_akhir_3": 0.0,
            "cabang_id_4": 5,
            "cabang_kode_4": "KRT",
            "cabang_nama_4": "CSB - Kairatu",
            "stok_awal_4": 0.0,
            "stok_masuk_4": 0.0,
            "stok_keluar_4": 0.0,
            "stok_akhir_4": 0.0,
            "cabang_id_5": 6,
            "cabang_kode_5": "KS",
            "cabang_nama_5": "DEV - Koffiesoft",
            "stok_awal_5": 0,
            "stok_masuk_5": 0,
            "stok_keluar_5": 0,
            "stok_akhir_5": 0,
            "cabang_id_6": 7,
            "cabang_kode_6": "PRU",
            "cabang_nama_6": "CSB - Piru",
            "stok_awal_6": 0,
            "stok_masuk_6": 0,
            "stok_keluar_6": 0,
            "stok_akhir_6": 0
        },

ini harus table baru ya dengan nama field yang jelas kemudian program nya juga harus baru di luar dari existing project ini ya 
