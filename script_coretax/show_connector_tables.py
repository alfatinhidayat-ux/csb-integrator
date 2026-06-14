import pymysql
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DB_CONFIG = {
    'host': os.getenv('BRIGHTER_DB_HOST', '127.0.0.1'),
    'port': int(os.getenv('BRIGHTER_DB_PORT', 3306)),
    'user': os.getenv('BRIGHTER_DB_USER', 'root'),
    'password': os.getenv('BRIGHTER_DB_PASSWORD', ''),
    'database': 'bright_connector',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

conn = pymysql.connect(**DB_CONFIG)
try:
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES")
        tables = [list(r.values())[0] for r in cur.fetchall()]
        print("Tables in bright_connector:")
        for t in sorted(tables):
            print(" -", t)
except Exception as e:
    print("Error:", e)
finally:
    conn.close()
