import os
import httpx
import pandas as pd
import pymysql
import io
import uuid
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("BRIGHTER_DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("BRIGHTER_DB_PORT", 3306))
DB_USER = os.getenv("BRIGHTER_DB_USER", "root")
DB_PASSWORD = os.getenv("BRIGHTER_DB_PASSWORD", "")
DB_NAME = os.getenv("BRIGHTER_DB_NAME", "brighter_mirror")
API_BASE_URL = os.getenv("BRIGHTER_BASE_URL", "https://brighter-kairatu-api.koffiesoft.com")
BEARER_TOKEN = os.getenv("BRIGHTER_TAX_TOKEN")

def get_db_connection():
    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        charset='utf8mb4'
    )
    with conn.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}`")
    conn.select_db(DB_NAME)
    return conn

def fetch_excel_path(url):
    headers = {"Authorization": f"Bearer {BEARER_TOKEN}"}
    print(f"Fetching from: {url}")
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if "data" in data and "xlsx_path" in data["data"]:
            return API_BASE_URL + data["data"]["xlsx_path"]
        raise Exception(f"Failed to get excel path: {data}")

def download_excel(excel_url):
    print(f"Downloading Excel: {excel_url}")
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(excel_url)
        resp.raise_for_status()
        return io.BytesIO(resp.content)

def clean_column_name(col):
    return str(col).strip().replace('/', '_').replace(' ', '_').lower()

def insert_dataframe(df, table_name, connection, batch_id, kategori, cabang_id=1):
    if df.empty:
        return
        
    # Hapus baris penanda "END" jika ada (biasanya di kolom pertama 'Baris')
    df = df[df.iloc[:, 0].astype(str).str.strip().str.upper() != 'END']
    
    if df.empty:
        return

    # Cast to object so None can replace float NaNs without being coerced back
    df = df.astype(object).where(pd.notnull(df), None)
    
    columns = list(df.columns)
    columns_clean = [clean_column_name(c) for c in columns]
    
    col_defs = ", ".join([f"`{c}` TEXT" for c in columns_clean])
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS `{table_name}` (
        `id` INT AUTO_INCREMENT PRIMARY KEY,
        `batch_id` VARCHAR(50),
        `kategori_tarikan` VARCHAR(50),
        `cabang_id` INT DEFAULT 1,
        `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        {col_defs}
    )
    """
    
    with connection.cursor() as cursor:
        cursor.execute(create_sql)
        
        # Tambahkan nilai batch_id, kategori, dan cabang_id ke setiap baris
        placeholders = ", ".join(["%s"] * (len(columns) + 3))
        cols_sql = "`batch_id`, `kategori_tarikan`, `cabang_id`, " + ", ".join([f"`{c}`" for c in columns_clean])
        insert_sql = f"INSERT INTO `{table_name}` ({cols_sql}) VALUES ({placeholders})"
        
        data_to_insert = [(batch_id, kategori, cabang_id) + tuple(row) for row in df.itertuples(index=False, name=None)]
        
        cursor.executemany(insert_sql, data_to_insert)
    connection.commit()
    print(f"Inserted {len(data_to_insert)} rows into `{table_name}`.")

def sync_url(url, kategori, cabang_id=1):
    try:
        # Generate ID unik untuk satu kali tarikan ini
        batch_id = str(uuid.uuid4())
        
        excel_url = fetch_excel_path(url)
        excel_content = download_excel(excel_url)
        
        print("Parsing 'Faktur' sheet...")
        df_faktur = pd.read_excel(excel_content, sheet_name="Faktur", header=2, dtype=str)
        
        excel_content.seek(0)
        print("Parsing 'DetailFaktur' sheet...")
        df_detail = pd.read_excel(excel_content, sheet_name="DetailFaktur", header=0, dtype=str)
        
        conn = get_db_connection()
        try:
            insert_dataframe(df_faktur, "coretax_faktur", conn, batch_id, kategori, cabang_id)
            insert_dataframe(df_detail, "coretax_detail_faktur", conn, batch_id, kategori, cabang_id)
        finally:
            conn.close()
            
    except Exception as e:
        print(f"Error syncing {url}: {e}")

if __name__ == "__main__":
    import argparse
    import json
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--cabang-ids", default="", help="Comma-separated list of cabang IDs to sync. Defaults to all active from cabang.json")
    args = parser.parse_args()
    
    cabang_ids = []
    if args.cabang_ids:
        cabang_ids = [c.strip() for c in args.cabang_ids.split(",") if c.strip()]
    else:
        # Load from cabang.json
        cabang_path = os.path.join(os.path.dirname(__file__), "cabang.json")
        if os.path.exists(cabang_path):
            with open(cabang_path, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                cabang_ids = [str(c["cabang_id"]) for c in cdata.get("data", []) if c.get("cabang_aktif") == "Aktif"]
        
        if not cabang_ids:
            cabang_ids = ["1"] # Fallback

    if not BEARER_TOKEN:
        print("BRIGHTER_TAX_TOKEN is not set in .env")
        exit(1)
        
    base_url = os.getenv("BRIGHTER_TAX_URL_NPWP")
    if not base_url:
        print("BRIGHTER_TAX_URL_NPWP is not set in .env")
        exit(1)
    
    # Hapus tabel lama setiap kali run agar data tidak menumpuk (seperti main.py clean_start)
    print("Membersihkan tabel lama...")
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS coretax_faktur")
        cursor.execute("DROP TABLE IF EXISTS coretax_detail_faktur")
    conn.commit()
    conn.close()
    
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

    for cid in cabang_ids:
        print(f"\n=== Syncing Cabang ID {cid} ===")
        # Parse URL and replace cabang_id parameter
        parsed_url = urlparse(base_url)
        query_params = parse_qs(parsed_url.query)
        query_params["cabang_id"] = [cid]
        new_query = urlencode(query_params, doseq=True)
        new_url = urlunparse(parsed_url._replace(query=new_query))
        
        print(f"--- Syncing SEMUA untuk Cabang {cid} ---")
        sync_url(new_url, kategori="semua", cabang_id=int(cid))
        print("-" * 40)

