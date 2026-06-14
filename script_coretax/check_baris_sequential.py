import pandas as pd

target_semua = "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/pos_coretax_20260614073145_semua.xlsx"
df_detail = pd.read_excel(target_semua, sheet_name="DetailFaktur", header=0, dtype=str)

# Clean up END row
df_detail = df_detail[df_detail['Baris'].astype(str).str.strip().str.upper() != 'END']

print("Is 'Baris' column in DetailFaktur unique?", df_detail['Baris'].is_unique)
print("Total rows in DetailFaktur:", len(df_detail))
print("Max value in 'Baris' column of DetailFaktur:", df_detail['Baris'].astype(float).max())
print("First 15 values in 'Baris' column of DetailFaktur:")
print(df_detail['Baris'].head(15).tolist())
