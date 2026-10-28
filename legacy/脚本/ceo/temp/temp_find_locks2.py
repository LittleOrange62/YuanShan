import psutil
for proc in psutil.process_iter(['pid','name','open_files']):
    try:
        ofs = proc.info['open_files'] or []
        for of in ofs:
            if 'tq_daily_2026' in of.path:
                print(f'PID {proc.info["pid"]} ({proc.info["name"]}): {of.path}')
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
