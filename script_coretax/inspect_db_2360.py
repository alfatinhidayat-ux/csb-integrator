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
        cur.execute("SELECT * FROM coretax_faktur WHERE baris = '2360'")
        print("Faktur row 2360:")
        for r in cur.fetchall():
            print(r)
        
        cur.execute("SELECT * FROM coretax_detail_faktur WHERE baris = '2360'")
        print("\nDetail rows 2360:")
        for r in cur.fetchall():
            print(r)
finally:
    conn.close()
