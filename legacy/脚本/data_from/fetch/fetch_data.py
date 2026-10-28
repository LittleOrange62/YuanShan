import sys, json, subprocess, os

def install(package):
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', package])

try:
    import akshare as ak
except ImportError:
    install('akshare')
    import akshare as ak

import pandas as pd

tickers = [
    "SH110073","SH110074","SH113037","SH113575","SH113597","SH113624","SH113632","SH113658","SH118011","SH118040","SH118053","SH501091","SH560000","SH600984","SH603221","SH880556","SH880595","SH880641","SH880751","SH880836","SH880851","SH880854","SH880890","SZ000004","SZ002808","SZ002898","SZ123059","SZ123061","SZ123188","SZ123251","SZ127079","SZ128119","SZ128121","SZ128124","SZ128125","SZ200706","SZ300176"
]

date_str = "20260813"

success = []
failure = []

output_dir = "daily_data"
os.makedirs(output_dir, exist_ok=True)

for ticker in tickers:
    market = ticker[:2].lower()
    code = ticker[2:]
    symbol = f"{market}{code}"
    try:
        df = ak.stock_zh_a_hist(symbol=symbol, start_date=date_str, end_date=date_str, adjust="")
        if df.empty:
            raise ValueError("No data returned")
        # Save to CSV
        csv_path = os.path.join(output_dir, f"{ticker}.csv")
        df.to_csv(csv_path, index=False)
        success.append(ticker)
    except Exception as e:
        failure.append({"ticker": ticker, "reason": str(e)})

summary = {"date": date_str, "success": success, "failure": failure}
print(json.dumps(summary, ensure_ascii=False, indent=2))
