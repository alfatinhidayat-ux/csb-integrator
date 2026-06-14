import pandas as pd
import glob
import os

files = [
    "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/pos_coretax_20260614073145_semua.xlsx",
    "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/pos_coretax_20260614073205_npwp.xlsx"
]

for target in files:
    if not os.path.exists(target):
        print(f"File not found: {target}")
        continue
        
    print(f"\n================ Inspecting {os.path.basename(target)} ================")
    df_faktur = pd.read_excel(target, sheet_name="Faktur", header=2, dtype=str)
    print("Faktur columns:", df_faktur.columns.tolist()[:15])
    
    df_detail = pd.read_excel(target, sheet_name="DetailFaktur", header=0, dtype=str)
    print("Detail columns:", df_detail.columns.tolist()[:15])
    
    # Fill NaN for NPWP/NIK Pembeli
    df_faktur['NPWP/NIK Pembeli'] = df_faktur['NPWP/NIK Pembeli'].fillna('')
    df_faktur['Jenis ID Pembeli'] = df_faktur['Jenis ID Pembeli'].fillna('')
    
    # Let's count where Jenis ID Pembeli is TIN and NPWP is not empty
    tin_valid = df_faktur[
        (df_faktur['Jenis ID Pembeli'] == 'TIN') & 
        (df_faktur['NPWP/NIK Pembeli'] != '') & 
        (df_faktur['NPWP/NIK Pembeli'] != '0000000000000000') &
        (~df_faktur['NPWP/NIK Pembeli'].str.contains('^[0\.\-]+$', na=False))
    ]
    print(f"TIN valid count: {len(tin_valid)}")
    if len(tin_valid) > 0:
        print("Sample valid TIN rows:")
        print(tin_valid[['Baris', 'Jenis ID Pembeli', 'NPWP/NIK Pembeli', 'Nama Pembeli']].head(5))
        
        # Look up detail for these valid TIN rows
        valid_baris = tin_valid['Baris'].tolist()
        matching_details = df_detail[df_detail['Baris'].isin(valid_baris)]
        print("\nMatching Details:")
        print(matching_details[['Baris', 'Nama Barang/Jasa', 'Harga Satuan', 'Jumlah Barang Jasa', 'DPP', 'PPN']].head(5))
    else:
        print("No valid TIN rows found.")
