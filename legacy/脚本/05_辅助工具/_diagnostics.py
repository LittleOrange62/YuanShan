# -*- coding: utf-8 -*-
"""诊断通达信本地数据新鲜度 + 检查各子目录最新文件修改时间 + 读 .day 末尾记录最新日期"""
import os, struct, datetime
from glob import glob

TDX = r"D:\new_tdx64"

# 1) 各 vipdoc 子目录最新文件修改时间
print("=" * 60)
print("【1】vipdoc 各子目录 .day 文件最新修改时间")
for sub in ["sh/lday", "sz/lday", "sh/fzline", "sz/fzline", "sh/lc1", "sz/lc1", "sh/lc5", "sz/lc5"]:
    pat = os.path.join(TDX, "vipdoc", sub.replace("/", os.sep), "*.day")
    files = glob(pat)
    if not files:
        print(f"  {sub:12s}: 无文件")
        continue
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    latest = files[0]
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(latest))
    # 统计有多少文件在 8/14 之后更新
    cutoff = datetime.datetime(2026, 8, 14, 0, 0).timestamp()
    recent = sum(1 for f in files if os.path.getmtime(f) > cutoff)
    print(f"  {sub:12s}: 共{len(files):5d}个 最新={os.path.basename(latest)} "
          f"mtime={mt:%Y-%m-%d %H:%M:%S} 8/14后更新={recent}")

# 2) 解析 .day 二进制, 看最后一条记录的日期
# 通达信日线 .day 格式: 每条 32 字节
# int32 date(YYYYMMDD) | float open | float high | float low | float close
# float amount | int volume | int reserved
print()
print("=" * 60)
print("【2】.day 文件末尾记录日期 (取 sh600036 / sz000001 等)")
samples = {
    "SH600036": os.path.join(TDX, "vipdoc", "sh", "lday", "sh600036.day"),
    "SH000001": os.path.join(TDX, "vipdoc", "sh", "lday", "sh000001.day"),
    "SH600000": os.path.join(TDX, "vipdoc", "sh", "lday", "sh600000.day"),
    "SZ000001": os.path.join(TDX, "vipdoc", "sz", "lday", "sz000001.day"),
    "SZ000002": os.path.join(TDX, "vipdoc", "sz", "lday", "sz000002.day"),
    "SZ300750": os.path.join(TDX, "vipdoc", "sz", "lday", "sz300750.day"),
}
for sym, path in samples.items():
    if not os.path.exists(path):
        print(f"  {sym}: 文件不存在 {path}")
        continue
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        rec = 32
        n = size // rec
        f.seek(size - rec)
        data = f.read(rec)
    date_int, o, h, l, c = struct.unpack("<Iffff", data[:20])
    print(f"  {sym}: 记录数={n} 末条日期={date_int} O={o:.2f} H={h:.2f} L={l:.2f} C={c:.2f}")

# 3) 当前系统时间
print()
print("=" * 60)
print(f"【3】当前时间: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
print(f"    8/14 周五(交易日) 收盘 15:00, 盘后数据下载通常 17:00-22:00")
print(f"    现在距 8/14 收盘已过: {(datetime.datetime.now() - datetime.datetime(2026,8,14,15,0)).total_seconds()/3600:.1f} 小时")
