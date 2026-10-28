import os
import sqlite3
from datetime import datetime

D = r'D:\finance\data'
conn = sqlite3.connect(os.path.join(D, 'tq_core.db'))
cur = conn.cursor()

# 主要指数信息
INDEX_CODES = {
    '000001.SH': '上证指数',
    '000300.SH': '沪深300',
    '000852.SH': '中证1000',
    '000905.SH': '中证500',
    '399001.SZ': '深证成指',
    '399006.SZ': '创业板指',
    '899050.BJ': '北证50',
    '000016.SH': '上证50',
    '000688.SH': '科创50',
    '399673.SZ': '创业板50'
}

# 插入指数信息
for ts_code, name in INDEX_CODES.items():
    pure_code = ts_code.split('.')[0]
    market = ts_code.split('.')[1]
    cur.execute('''
        INSERT OR REPLACE INTO index_info (ts_code, symbol, name, market, base_date, base_point, remarks, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (ts_code, pure_code, name, market, None, None, '主要宽基指数', 'tq', datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

# 扫描股票基础信息（简化版，只存代码和市场，其他后续补充）
stock_count = 0
for ex in ['sh', 'sz', 'bj']:
    lday_dir = f'D:/new_tdx64/vipdoc/{ex}/lday'
    if not os.path.exists(lday_dir):
        continue
    for f in os.listdir(lday_dir):
        if f.endswith('.day'):
            code = f.replace('.day', '')
            if code.startswith(ex):
                pure_code = code[2:]
                if pure_code.isdigit() and len(pure_code) == 6:
                    ts_code = f'{pure_code}.{ex.upper()}'
                    if ts_code not in INDEX_CODES:
                        cur.execute('''
                            INSERT OR IGNORE INTO stock_info (ts_code, symbol, name, market, asset_type, list_date, source, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (ts_code, pure_code, None, ex.upper(), 'stock', None, 'tq', datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                        stock_count += 1

conn.commit()
print(f'Inserted {stock_count} stocks into stock_info')
print(f'Inserted {len(INDEX_CODES)} indices into index_info')

# 插入元数据说明
meta = [
    ('stock_info', '股票基础信息表', '{"ts_code":"股票代码","symbol":"原始代码","name":"股票名称","market":"市场","asset_type":"资产类型","list_date":"上市日期","delist_date":"退市日期","industry":"所属行业","area":"地域","total_shares":"总股本","float_shares":"流通股本","list_status":"上市状态","is_hs":"沪深港通","remarks":"备注","source":"数据来源","updated_at":"更新时间"}'),
    ('index_info', '指数基础信息表', '{"ts_code":"指数代码","symbol":"原始代码","name":"指数名称","market":"市场","base_date":"基日","base_point":"基点","remarks":"备注","source":"数据来源","updated_at":"更新时间"}'),
    ('watchlist_groups', '自选股分组表', '{"group_id":"分组ID","name":"分组名称","description":"分组说明","parent_id":"父分组ID","sort_order":"排序","created_at":"创建时间","updated_at":"更新时间"}'),
    ('watchlist_stocks', '自选股分组明细表', '{"id":"自增ID","group_id":"分组ID","ts_code":"股票代码","name":"股票名称","sort_order":"排序","added_at":"加入时间","notes":"备注"}'),
    ('focus_stocks', '重点股表', '{"id":"自增ID","ts_code":"股票代码","name":"股票名称","notes":"备注","sort_order":"排序","added_at":"加入时间","updated_at":"更新时间"}'),
    ('stock_daily', '个股日线行情表', '{"ts_code":"股票代码","trade_date":"交易日期","open":"开盘价","high":"最高价","low":"最低价","close":"收盘价","pre_close":"昨收","change":"涨跌额","pct_change":"涨跌幅","vol":"成交量","amount":"成交额","turnover_rate":"换手率","total_mv":"总市值","float_mv":"流通市值","total_share":"总股本","float_share":"流通股本","adj_factor":"复权因子","limit_up":"涨停价","limit_down":"跌停价","source":"数据来源","updated_at":"更新时间"}'),
    ('index_daily', '指数日线行情表', '{"ts_code":"指数代码","trade_date":"交易日期","name":"指数名称","open":"开盘价","high":"最高价","low":"最低价","close":"收盘价","pre_close":"昨收","change":"涨跌额","pct_change":"涨跌幅","vol":"成交量","amount":"成交额","source":"数据来源","updated_at":"更新时间"}'),
    ('kline_factors', 'K线计算因子表', '{"ts_code":"股票代码","trade_date":"交易日期","ma5":"5日均线","ma10":"10日均线","ma20":"20日均线","ma60":"60日均线","pct_change_5d":"5日涨跌","pct_change_10d":"10日涨跌","pct_change_20d":"20日涨跌","amplitude_10d":"10日振幅","amplitude_20d":"20日振幅","daily_pct_change":"当日涨跌幅","daily_amplitude":"当日振幅","high20_prev":"20日最高","low10":"10日最低","high10":"10日最高","clv":"收盘位置","slope20":"20日斜率","ma10_ma20_dist":"10日20日均线距离","price_ma20_dist":"收盘价与20日均线距离","pos_pct_52w":"52周位置","hi52":"52周最高","lo52":"52周最低","dd52":"52周回撤","overhang_share_60d":"60日顶底抛压","hi60":"60日最高","dist_hi60":"距离60日最高","v5_avg":"5日均量","v20_avg":"20日均量","vr":"量比","dayv":"当日成交量","v5prev":"5日前成交量","dayr":"日振幅","xmorph":"形态因子","xtrend":"趋势因子","xpos":"位置因子","xchip":"筹码因子","vhealth":"量能因子","x_total":"X因子总分","created_at":"计算时间"}'),
    ('size_divergence', '大小盘分化指数表', '{"trade_date":"交易日期","zz1000_close":"中证1000收盘","hs300_close":"沪深300收盘","divergence":"分化值","divergence_pct":"分化比例","ma5_divergence":"5日分化均线","ma10_divergence":"10日分化均线","ma20_divergence":"20日分化均线","slope_5d":"5日趋势斜率","slope_10d":"10日趋势斜率","slope_20d":"20日趋势斜率","signal":"趋势信号","source":"数据来源","updated_at":"更新时间"}')
]

for table_name, table_comment, column_comment in meta:
    cur.execute('''
        INSERT OR REPLACE INTO meta_tables (table_name, table_comment, column_comment, updated_at)
        VALUES (?, ?, ?, ?)
    ''', (table_name, table_comment, column_comment, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

conn.commit()
conn.close()
print('Meta info inserted.')
