import pandas as pd

target_semua = "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/pos_coretax_20260614073145_semua.xlsx"
df_detail = pd.read_excel(target_semua, sheet_name="DetailFaktur", header=0, dtype=str)

matching_details = df_detail[df_detail['Nama Barang/Jasa'].str.contains('DAT ULTIMATE', na=False)]
print("Matching details in SEMUA:")
print(matching_details)
