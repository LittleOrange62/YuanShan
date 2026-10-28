import psutil
for proc in psutil.process_iter(['pid','name','cmdline']):
    try:
        cmd = ' '.join(proc.info['cmdline'] or [])
        if 'temp_fetch_all_daily' in cmd or 'tq_daily_2026' in cmd:
            print(f'PID {proc.info["pid"]}: {cmd}')
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
