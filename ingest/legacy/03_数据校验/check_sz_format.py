import struct, os

# Let me check both SH and SZ with the integer price format
# SH600000 (Pudong Development Bank) - well known stock

files_to_check = [
    (r'D:\new_tdx64\vipdoc\sh\lday\sh600000.day', 'SH 600000'),
    (r'D:\new_tdx64\vipdoc\sz\lday\sz000001.day', 'SZ 000001'),
    (r'D:\new_tdx64\vipdoc\sz\lday\sz399001.day', 'SZ 399001'),
    (r'D:\new_tdx64\vipdoc\sh\lday\sh600519.day', 'SH 600519 Moutai'),
]

for path, label in files_to_check:
    if not os.path.exists(path):
        print(f"SKIP {label} - not found")
        continue
    raw = open(path, 'rb').read(128)
    fsize = os.path.getsize(path)
    print(f"\n=== {label} ({fsize} bytes, {fsize//32} records) ===")
    
    for rec in range(min(3, len(raw)//32)):
        chunk = raw[rec*32:(rec+1)*32]
        print(f"\n  Rec {rec}: {chunk.hex()}")
        
        # User format: I date + f o + f h + f l + f c + f amount + I vol + I reserved
        try:
            d = struct.unpack('IffffffII', chunk)
            print(f"    User(IfffffII): date={d[0]}, O={d[1]:.4f}, H={d[2]:.4f}, L={d[3]:.4f}, C={d[4]:.4f}, amt={d[5]:.0f}, vol={d[6]}, swap={d[7]}")
        except Exception as e:
            print(f"    User(IfffffII): {e}")
        
        # INT format: I date + I o + I h + I l + I c + f amount + I vol + I swap
        try:
            d = struct.unpack('IIIIIIfI', chunk)
            print(f"    INT(IiiiiIfI): date={d[0]}, O={d[1]/100:.2f}, H={d[2]/100:.2f}, L={d[3]/100:.2f}, C={d[4]/100:.2f}, amt={d[5]:.0f}, vol={d[6]}, swap={d[7]}")
        except Exception as e:
            print(f"    INT(IiiiiIfI): {e}")
        
        # Try: I date + I o + I h + I l + I c + I vol + f amount + I swap
        try:
            d = struct.unpack('IIIIIfII', chunk)
            print(f"    INT2(IiiiIiII): date={d[0]}, O={d[1]/100:.2f}, H={d[2]/100:.2f}, L={d[3]/100:.2f}, C={d[4]/100:.2f}, vol={d[5]}, amt={d[6]:.0f}, swap={d[7]}")
        except Exception as e:
            print(f"    INT2(IiiiIiII): {e}")
