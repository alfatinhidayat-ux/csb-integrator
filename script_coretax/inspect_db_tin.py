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
        # Count all TIN rows
        cur.execute("SELECT COUNT(*) as cnt FROM coretax_faktur WHERE jenis_id_pembeli = 'TIN'")
        print("Total TIN rows in Faktur:", cur.fetchone()['cnt'])
        
        # Count TIN rows with npwp_nik_pembeli filled
        cur.execute("SELECT COUNT(*) as cnt FROM coretax_faktur WHERE jenis_id_pembeli = 'TIN' AND npwp_nik_pembeli IS NOT NULL AND npwp_nik_pembeli != ''")
        print("TIN rows with npwp filled:", cur.fetchone()['cnt'])
        
        # Select some TIN rows with npwp filled
        cur.execute("SELECT * FROM coretax_faktur WHERE jenis_id_pembeli = 'TIN' AND npwp_nik_pembeli IS NOT NULL AND npwp_nik_pembeli != '' LIMIT 10")
        print("\nSample TIN rows with npwp filled:")
        for r in cur.fetchall():
            print(f"ID: {r['id']}, Baris: {r['baris']}, NPWP: {r['npwp_nik_pembeli']}, Nama: {r['nama_pembeli']}")
finally:
    conn.close()
