import pandas as pd

target_semua = "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/pos_coretax_20260614073145_semua.xlsx"
df_faktur = pd.read_excel(target_semua, sheet_name="Faktur", header=2, dtype=str)
df_detail = pd.read_excel(target_semua, sheet_name="DetailFaktur", header=0, dtype=str)

print("Faktur rows 1845-1855 in SEMUA:")
print(df_faktur[['Baris', 'NPWP/NIK Pembeli', 'Jenis ID Pembeli', 'Nama Pembeli', 'Referensi']].iloc[1845:1855])

print("\nDetail rows 1845-1855 in SEMUA:")
print(df_detail[['Baris', 'Nama Barang/Jasa', 'Harga Satuan', 'Jumlah Barang Jasa', 'DPP']].iloc[1845:1855])
