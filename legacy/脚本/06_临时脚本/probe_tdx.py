import os, sqlite3

# 1. BlockMapXML.dat
fn = r'D:\new_tdx64\BlockMap\BlockMapXML.dat'
with open(fn, 'rb') as f:
    raw = f.read(5000)
print('=== BlockMapXML.dat (%d bytes) ===' % len(raw))
print('Hex head 100:', raw[:100].hex())
try:
    print(raw.decode('utf-8')[:800])
except Exception as e:
    print('UTF8 err:', e)
try:
    print('\n--- GBK ---')
    print(raw.decode('gbk')[:800])
except Exception as e:
    print('GBK err:', e)
print()

# 2. .day file
fn2 = r'D:\new_tdx64\vipdoc\sh\lday\sh880001.day'
with open(fn2, 'rb') as f:
    raw2 = f.read(1000)
sz = os.path.getsize(fn2)
print('=== sh880001.day (%d bytes) ===' % sz)
print('Hex head 100:', raw2[:100].hex())
try:
    print(raw2.decode('gbk')[:800])
except Exception as e:
    print('GBK err:', e)
print()

# 3. hq_cache dat files
for nm in ['infoharbor_block.dat','csiblock.dat','jjblock.dat','mgblock.dat','sbblock.dat','relation.dat','hspy.dat']:
    fp = os.path.join(r'D:\new_tdx64\T0002\hq_cache', nm)
    if not os.path.exists(fp):
        continue
    with open(fp, 'rb') as f:
        raw = f.read(1000)
    fsz = os.path.getsize(fp)
    print('=== %s (%d bytes) ===' % (nm, fsz))
    try:
        print(raw.decode('gbk')[:500])
    except Exception as e:
        print('GBK err:', e, 'hex:', raw[:60].hex())
    print()
