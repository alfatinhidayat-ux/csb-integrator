import pandas as pd

target_semua = "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/pos_coretax_20260614073145_semua.xlsx"
df_faktur = pd.read_excel(target_semua, sheet_name="Faktur", header=2, dtype=str)
df_detail = pd.read_excel(target_semua, sheet_name="DetailFaktur", header=0, dtype=str)

print("--- FAKTUR FIRST 5 ROWS ---")
print(df_faktur.head(5).to_string())

print("\n--- DETAIL FIRST 10 ROWS ---")
print(df_detail.head(10).to_string())
