import pandas as pd
import os

target = "/Users/alfathinhidayatulloh/Sites/cbs-project/csb-integrator/script_coretax/ConverterEfakturCoretax_v1.6/TemplateExcel/Sample Faktur PK Template v.1.6.1.xlsx"
if os.path.exists(target):
    xl = pd.ExcelFile(target)
    print("Sheets in Sample Template:", xl.sheet_names)
    df_faktur = pd.read_excel(xl, sheet_name="Faktur", header=2, dtype=str)
    df_detail = pd.read_excel(xl, sheet_name="DetailFaktur", header=0, dtype=str)
    
    print("\nFaktur columns in Sample:")
    print(df_faktur.columns.tolist()[:15])
    print("\nDetail columns in Sample:")
    print(df_detail.columns.tolist()[:15])
    
    print("\nFaktur sheet sample rows:")
    print(df_faktur.head(3))
    print("\nDetail sheet sample rows:")
    print(df_detail.head(3))
else:
    print("Sample template not found")
