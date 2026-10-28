# -*- coding: utf-8 -*-
import struct

for f in [r'D:\new_tdx64\vipdoc\sz\lday\sz127033.day',
          r'D:\new_tdx64\vipdoc\sz\lday\sz128044.day',
          r'D:\new_tdx64\vipdoc\sh\lday\sh501023.day']:
    try:
        with open(f, 'rb') as fp:
            fp.seek(0, 2)
            size = fp.tell()
            fp.seek(0)
            hdr = fp.read(32)
            fp.seek(size - 32)
            tail = fp.read(32)
        d1 = struct.unpack('<I', hdr[:4])[0]
        d2 = struct.unpack('<I', tail[:4])[0]
        print(f'{f.split(chr(92))[-1]}: {size//32} 条 [{d1}~{d2}]')
    except Exception as e:
        print(f, e)
