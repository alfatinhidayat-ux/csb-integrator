import pandas as pd

target_semua = "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/pos_coretax_20260614073145_semua.xlsx"
df_faktur = pd.read_excel(target_semua, sheet_name="Faktur", header=2, dtype=str)

row_1852 = df_faktur[df_faktur['Baris'] == '1852']
print("Faktur row 1852:")
print(row_1852[['Baris', 'NPWP/NIK Pembeli', 'Jenis ID Pembeli', 'Nama Pembeli', 'Referensi']])
