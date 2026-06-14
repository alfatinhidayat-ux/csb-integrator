import os, glob
import xml.etree.ElementTree as ET

dpp_sum = 0
for f in glob.glob('output_xml/*.xml'):
    root = ET.parse(f).getroot()
    cabang_dpp = 0
    for gs in root.iter('GoodService'):
        cabang_dpp += float(gs.find('TaxBase').text)
    dpp_sum += cabang_dpp
    print(f"File {os.path.basename(f)} DPP: {cabang_dpp:,.2f}")
print(f"Total DPP in all XMLs: {dpp_sum:,.2f}")
