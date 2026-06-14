import httpx
import pandas as pd
import io

url = "https://brighter-kairatu-api.koffiesoft.com/transaksi/pos/export/coretax?tanggal_awal=2026-05-01&tanggal_akhir=2026-05-31&opsi_ktp_npwp=semua&cabang_id=1&timezone=Asia%2FJakarta"
token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJOSU5HUlVNIiwidXNlcl9pZCI6MjEsInVzZXJfbmFtZSI6Ik5JTkdSVU0iLCJncnVwIjoiQWRtaW5pc3RyYXRvciIsInNlc3Npb25faWQiOjI0NTUsImV4cCI6MTc4Mzk4ODQ4OH0.LC279nHsHQDPxz4bNUYbmm5LHbIK4un7lwzV6prDVIs"

headers = {"Authorization": f"Bearer {token}"}
resp = httpx.get(url, headers=headers)
data = resp.json()

if "data" in data and "xlsx_path" in data["data"]:
    excel_url = "https://brighter-kairatu-api.koffiesoft.com" + data["data"]["xlsx_path"]
    print(f"Downloading excel from: {excel_url}")
    excel_resp = httpx.get(excel_url)
    df = pd.read_excel(io.BytesIO(excel_resp.content))
    print("Columns in Excel:")
    print(df.columns.tolist())
    print("First row data:")
    print(df.iloc[0].to_dict() if len(df) > 0 else "Empty dataframe")
else:
    print("Error getting excel path:", data)
