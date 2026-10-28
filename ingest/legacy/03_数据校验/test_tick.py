from pytdx.hq import TdxHq_API
import pandas as pd
symbol='600036'
servers=[('110.41.147.114',7709),('124.70.199.56',7709),('121.36.225.169',7709),('123.60.70.228',7709),('123.60.73.44',7709)]
for ip,port in servers:
    try:
        api=TdxHq_API(raise_exception=True)
        if api.connect(ip,port,time_out=8):
            market=1 if symbol.startswith(('6','9','5')) else 0
            data=api.get_minute_time_data(market,symbol)
            api.disconnect()
            if data:
                df=pd.DataFrame(data)
                print('Connected',ip)
                print('Columns:',df.columns.tolist())
                print(df.head())
                break
    except Exception as e:
        print('fail',ip,e)
