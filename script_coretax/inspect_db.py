import pymysql
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DB_CONFIG = {
    'host': os.getenv('BRIGHTER_DB_HOST', '127.0.0.1'),
    'port': int(os.getenv('BRIGHTER_DB_PORT', 3306)),
    'user': os.getenv('BRIGHTER_DB_USER', 'root'),
    'password': os.getenv('BRIGHTER_DB_PASSWORD', ''),
    'database': os.getenv('BRIGHTER_DB_NAME', 'brighter_mirror'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

conn = pymysql.connect(**DB_CONFIG)
try:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM coretax_faktur WHERE jenis_id_pembeli = 'TIN'")
        rows = cur.fetchall()
        print("TIN Faktur Rows:")
        for r in rows:
            print(f"ID: {r['id']}, Cabang: {r['cabang_id']}, Baris: {r['baris']}, NPWP/NIK: {r['npwp_nik_pembeli']}, Nama: {r['nama_pembeli']}")
            
        cur.execute("SELECT * FROM coretax_detail_faktur WHERE baris IN (SELECT baris FROM coretax_faktur WHERE jenis_id_pembeli = 'TIN')")
        details = cur.fetchall()
        print("\nDetail Rows matching TIN:")
        for d in details:
            print(f"ID: {d['id']}, Cabang: {d['cabang_id']}, Baris: {d['baris']}, Nama Barang: {d['nama_barang_jasa']}, Harga: {d.get('harga_satuan', '')}, Qty: {d.get('jumlah_barang_jasa', d.get('jumlah_barang', ''))}")
finally:
    conn.close()
