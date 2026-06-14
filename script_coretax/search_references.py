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
        # Search in all tables
        cur.execute("SHOW TABLES")
        tables = [list(r.values())[0] for r in cur.fetchall()]
        
        search_terms = ['SB/V2/2605-0495', 'SB/PT/2605-0094']
        for table in tables:
            # Get text columns of the table
            cur.execute(f"DESCRIBE `{table}`")
            columns = cur.fetchall()
            text_cols = [c['Field'] for c in columns if any(x in c['Type'].lower() for x in ['varchar', 'text', 'char'])]
            
            if not text_cols:
                continue
                
            for term in search_terms:
                or_conds = " OR ".join([f"`{col}` LIKE %s" for col in text_cols])
                params = [f"%{term}%" for _ in text_cols]
                query = f"SELECT * FROM `{table}` WHERE {or_conds} LIMIT 5"
                
                cur.execute(query, params)
                results = cur.fetchall()
                if results:
                    print(f"Found match for '{term}' in table '{table}':")
                    for r in results:
                        print(r)
finally:
    conn.close()
