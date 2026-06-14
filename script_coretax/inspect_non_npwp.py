import pandas as pd
import os

target = "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/pos_coretax_20260614073542_non_npwp.xlsx"
if os.path.exists(target):
    df_faktur = pd.read_excel(target, sheet_name="Faktur", header=2, dtype=str)
    df_detail = pd.read_excel(target, sheet_name="DetailFaktur", header=0, dtype=str)
    
    # Check for SB/V2/2605-0495
    f_match1 = df_faktur[df_faktur['Referensi'] == 'SB/V2/2605-0495']
    print("SB/V2/2605-0495 in NON-NPWP Faktur:")
    print(f_match1[['Baris', 'NPWP/NIK Pembeli', 'Jenis ID Pembeli', 'Nama Pembeli', 'Referensi']])
    
    # Check for SB/PT/2605-0094
    f_match2 = df_faktur[df_faktur['Referensi'] == 'SB/PT/2605-0094']
    print("\nSB/PT/2605-0094 in NON-NPWP Faktur:")
    print(f_match2[['Baris', 'NPWP/NIK Pembeli', 'Jenis ID Pembeli', 'Nama Pembeli', 'Referensi']])
    
    # Check for DAT ULTIMATE in details
    d_match1 = df_detail[df_detail['Nama Barang/Jasa'].str.contains('DAT ULTIMATE', na=False)]
    print("\nDAT ULTIMATE in NON-NPWP Details:")
    print(d_match1[['Baris', 'Nama Barang/Jasa', 'Harga Satuan', 'Jumlah Barang Jasa', 'DPP']])
    
    # Check for SEMEN CONCH in details matching the baris
    d_match2 = df_detail[df_detail['Nama Barang/Jasa'].str.contains('SEMEN CONCH', na=False)]
    print("\nSEMEN CONCH in NON-NPWP Details (first 5):")
    print(d_match2[['Baris', 'Nama Barang/Jasa', 'Harga Satuan', 'Jumlah Barang Jasa', 'DPP']].head(5))
else:
    print("File not found:", target)
