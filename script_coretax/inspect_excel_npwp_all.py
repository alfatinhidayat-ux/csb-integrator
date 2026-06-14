import pandas as pd

target_npwp = "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/pos_coretax_20260614073205_npwp.xlsx"
df_faktur = pd.read_excel(target_npwp, sheet_name="Faktur", header=2, dtype=str)

print("Faktur in NPWP Excel:")
print(df_faktur[['Baris', 'NPWP/NIK Pembeli', 'Jenis ID Pembeli', 'Nama Pembeli', 'Referensi']])
