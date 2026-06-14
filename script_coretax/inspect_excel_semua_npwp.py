import pandas as pd

target_semua = "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/pos_coretax_20260614073145_semua.xlsx"
df_faktur = pd.read_excel(target_semua, sheet_name="Faktur", header=2, dtype=str)

df_faktur['NPWP/NIK Pembeli'] = df_faktur['NPWP/NIK Pembeli'].fillna('')
matching_rows = df_faktur[df_faktur['NPWP/NIK Pembeli'].str.contains('8101060207740002')]

print("All rows in SEMUA matching NPWP 8101060207740002:")
print(matching_rows[['Baris', 'Tanggal Faktur', 'Referensi', 'NPWP/NIK Pembeli', 'Nama Pembeli']])
