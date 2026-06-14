import os, pymysql
from dotenv import load_dotenv

load_dotenv()
conn = pymysql.connect(
    host=os.getenv('BRIGHTER_DB_HOST', 'localhost'),
    port=int(os.getenv('BRIGHTER_DB_PORT', 3306)),
    user=os.getenv('BRIGHTER_DB_USER', 'root'),
    password=os.getenv('BRIGHTER_DB_PASSWORD', ''),
    db=os.getenv('BRIGHTER_DB_NAME', 'brighter_mirror'),
    cursorclass=pymysql.cursors.DictCursor
)

with conn.cursor() as cur:
    cur.execute("""
        SELECT f.cabang_id, SUM(CAST(d.dpp AS DECIMAL(15,2))) as total_dpp
        FROM coretax_faktur f
        JOIN coretax_detail_faktur d ON f.batch_id = d.batch_id AND f.baris = d.baris
        WHERE f.npwp_nik_pembeli IS NOT NULL 
          AND f.jenis_id_pembeli = 'TIN'
        GROUP BY f.cabang_id
    """)
    db_results = cur.fetchall()

for row in db_results:
    print(f"Cabang {row['cabang_id']} DB DPP: {row['total_dpp']:,.2f}")

