import pandas as pd
import os

target = "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/pos_coretax_20260614073542_non_npwp.xlsx"
df_detail = pd.read_excel(target, sheet_name="DetailFaktur", header=0, dtype=str)

match_1851 = df_detail[df_detail['Baris'] == '1851']
print("Detail for Baris 1851 in NON-NPWP:")
print(match_1851[['Baris', 'Nama Barang/Jasa', 'Harga Satuan', 'Jumlah Barang Jasa', 'DPP']])
