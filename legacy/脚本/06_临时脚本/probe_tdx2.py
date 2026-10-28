import zipfile, os

# 1. BlockMapXML.dat is a ZIP file
zf = zipfile.ZipFile(r'D:\new_tdx64\BlockMap\BlockMapXML.dat')
print('ZIP contents:')
for info in zf.infolist():
    print(' ', info.filename, info.file_size)

# Extract blockmap.xml
xml_content = zf.read('blockmap.xml')
print('\n=== blockmap.xml (%d bytes) ===' % len(xml_content))
try:
    print(xml_content.decode('utf-8')[:3000])
except Exception as e:
    print('UTF8 err:', e)
    try:
        print(xml_content.decode('gbk')[:3000])
    except Exception as e2:
        print('GBK err:', e2)
        print('Hex:', xml_content[:200].hex())

zf.close()
