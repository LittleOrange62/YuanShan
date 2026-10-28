import os, struct, sqlite3, glob

# ============================
# 1. Explore all file formats
# ============================

print("=" * 60)
print("1. BlockMapXML.dat location check")
print("=" * 60)
for base in [r'D:\new_tdx64\T0002']:
    for root, dirs, files in os.walk(base):
        for f in files:
            if 'block' in f.lower():
                fp = os.path.join(root, f)
                sz = os.path.getsize(fp)
                print(f"  {fp} ({sz} bytes)")
                if sz < 10000:
                    with open(fp, 'rb') as fh:
                        print(f"    content: {fh.read(200)}")
        break

print("\n" + "=" * 60)
print("2. hq_cache block dat files")
print("=" * 60)
hq_path = r'D:\new_tdx64\T0002\hq_cache'
for fn in sorted(os.listdir(hq_path)):
    if fn.endswith('.dat'):
        fp = os.path.join(hq_path, fn)
        sz = os.path.getsize(fp)
        print(f"  {fn}: {sz} bytes")
        with open(fp, 'rb') as f:
            chunk = f.read(min(300, sz))
        print(f"    head: {repr(chunk[:200])}")
        print()

print("\n" + "=" * 60)
print("3. sh880001.day sample")
print("=" * 60)
p = r'D:\new_tdx64\vipdoc\sh\lday\sh880001.day'
with open(p, 'rb') as f:
    d = f.read()
print(f"  size: {len(d)}")
print(f"  content: {d[:600].decode('gbk','replace')}")

print("\n" + "=" * 60)
print("4. sh880002.day sample")
print("=" * 60)
p = r'D:\new_tdx64\vipdoc\sh\lday\sh880002.day'
with open(p, 'rb') as f:
    d = f.read()
print(f"  size: {len(d)}")
print(f"  content: {d[:600].decode('gbk','replace')}")

print("\n" + "=" * 60)
print("5. sh880515.day (GN_ one from first line)")
print("=" * 60)
p = r'D:\new_tdx64\vipdoc\sh\lday\sh880515.day'
with open(p, 'rb') as f:
    d = f.read()
print(f"  size: {len(d)}")
print(f"  content: {d[:600].decode('gbk','replace')}")

# Count all sh880*.day
files = glob.glob(r'D:\new_tdx64\vipdoc\sh\lday\sh880*.day')
print(f"\nTotal sh880*.day files: {len(files)}")
