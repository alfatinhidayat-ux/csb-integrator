import pandas as pd

target_semua = "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/pos_coretax_20260614073145_semua.xlsx"
df_faktur = pd.read_excel(target_semua, sheet_name="Faktur", header=2, dtype=str)

print("All Faktur columns in SEMUA:")
print(df_faktur.columns.tolist())
