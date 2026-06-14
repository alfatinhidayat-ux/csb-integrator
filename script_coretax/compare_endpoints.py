import os
import httpx
import pandas as pd
from dotenv import load_dotenv
import io

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

token = os.getenv("BRIGHTER_TAX_TOKEN")
headers = {"Authorization": f"Bearer {token}"}
base_api_url = os.getenv("BRIGHTER_BASE_URL", "https://brighter-kairatu-api.koffiesoft.com")

url_semua_raw = os.getenv("BRIGHTER_TAX_URL_SEMUA")
url_npwp_raw = os.getenv("BRIGHTER_TAX_URL_NPWP")

def get_excel_df(url, name):
    print(f"Fetching {name} URL: {url}")
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        xlsx_path = data["data"]["xlsx_path"]
        excel_url = base_api_url + xlsx_path
        print(f"Downloading Excel from {excel_url}...")
        r_excel = client.get(excel_url)
        r_excel.raise_for_status()
        
        xl = pd.ExcelFile(io.BytesIO(r_excel.content))
        df_faktur = pd.read_excel(xl, sheet_name="Faktur", header=2, dtype=str)
        df_detail = pd.read_excel(xl, sheet_name="DetailFaktur", header=0, dtype=str)
        return df_faktur, df_detail

f_semua, d_semua = get_excel_df(url_semua_raw, "SEMUA")
f_npwp, d_npwp = get_excel_df(url_npwp_raw, "NPWP")

print("\n--- SEMUA ---")
match_semua_f = f_semua[f_semua['Referensi'] == 'SB/V2/2605-0495']
print("Faktur:")
print(match_semua_f[['Baris', 'NPWP/NIK Pembeli', 'Jenis ID Pembeli', 'Nama Pembeli', 'Referensi']])
if not match_semua_f.empty:
    baris_semua = match_semua_f['Baris'].iloc[0]
    match_semua_d = d_semua[d_semua['Baris'] == baris_semua]
    print("Detail:")
    print(match_semua_d[['Baris', 'Nama Barang/Jasa', 'Harga Satuan', 'Jumlah Barang Jasa', 'DPP', 'PPN']])

print("\n--- NPWP ---")
match_npwp_f = f_npwp[f_npwp['Referensi'] == 'SB/V2/2605-0495']
print("Faktur:")
print(match_npwp_f[['Baris', 'NPWP/NIK Pembeli', 'Jenis ID Pembeli', 'Nama Pembeli', 'Referensi']])
if not match_npwp_f.empty:
    baris_npwp = match_npwp_f['Baris'].iloc[0]
    match_npwp_d = d_npwp[d_npwp['Baris'] == baris_npwp]
    print("Detail:")
    print(match_npwp_d[['Baris', 'Nama Barang/Jasa', 'Harga Satuan', 'Jumlah Barang Jasa', 'DPP', 'PPN']])
