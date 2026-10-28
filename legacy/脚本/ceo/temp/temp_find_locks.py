import psutil, os
d = r'D:\finance\data'
files = [f for f in os.listdir(d) if f.endswith('.db')]
for f in files:
    fp = os.path.join(d, f)
    for proc in psutil.process_iter(['pid','name','open_files']):
        try:
            ofs = proc.info['open_files'] or []
            if any(of.path == fp for of in ofs):
                print(f'File {f} is used by PID {proc.info["pid"]} ({proc.info["name"]})')
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
