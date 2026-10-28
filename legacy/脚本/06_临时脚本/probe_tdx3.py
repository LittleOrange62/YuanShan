import zipfile, os, xml.etree.ElementTree as ET

zf = zipfile.ZipFile(r'D:\new_tdx64\BlockMap\BlockMapXML.dat')

# Extract and parse key XMLs
for nm in ['行业板块.xml', '概念板块.xml', '风格板块.xml', '地区板块.xml']:
    try:
        content = zf.read(nm)
        text = content.decode('utf-8')
        print('=== %s (%d bytes) ===' % (nm, len(text)))
        print(text[:3000])
        print('...')
        print()
    except Exception as e:
        print('%s error: %s' % (nm, e))
        print()

zf.close()
