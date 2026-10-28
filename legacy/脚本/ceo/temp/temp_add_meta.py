import sqlite3
import os
from datetime import datetime

D = r'D:\finance\data'

# 定义所有表的元数据（表描述、字段描述）
TABLE_META = {
    'tq_core.db': {
        'stock_info': {
            'comment': '股票基础信息表（静态）',
            'columns': {
                'ts_code': '股票代码（主键），格式 600519.SH',
                'symbol': '原始代码，如 600519',
                'name': '股票名称，如 贵州茅台',
                'market': '市场代码（SH/SZ/BJ）',
                'asset_type': '资产类型（stock/index/fund/bond）',
                'list_date': '上市日期 YYYYMMDD',
                'delist_date': '退市日期 YYYYMMDD，空值表示仍在上市',
                'industry': '所属行业，互联网补充数据，定期更新',
                'area': '地域（省份/直辖市）',
                'total_shares': '总股本（股）',
                'float_shares': '流通股本（股）',
                'list_status': '上市状态（L=上市/D=退市/P=暂停）',
                'is_hs': '沪深港通标的（N=否/H=沪股通/S=深股通）',
                'remarks': '备注（ST标识、特殊处理等）',
                'source': '数据来源（tq/internet）',
                'updated_at': '更新时间 YYYY-MM-DD HH:MM:SS'
            }
        },
        'index_info': {
            'comment': '指数基础信息表（静态）',
            'columns': {
                'ts_code': '指数代码（主键），格式 000300.SH',
                'symbol': '原始代码，如 000300',
                'name': '指数名称，如 沪深300',
                'market': '市场代码（SH/SZ/BJ）',
                'base_date': '基日 YYYYMMDD',
                'base_point': '基点数值',
                'remarks': '备注（编制方案、成分调整等）',
                'source': '数据来源',
                'updated_at': '更新时间'
            }
        },
        'watchlist_groups': {
            'comment': '自选股分组定义表',
            'columns': {
                'group_id': '分组ID（主键自增）',
                'name': '分组名称，如 半导体产业链',
                'description': '分组说明（产业传导逻辑、投资主线等）',
                'parent_id': '父分组ID，0表示顶级分组',
                'sort_order': '排序号',
                'created_at': '创建时间',
                'updated_at': '更新时间'
            }
        },
        'watchlist_stocks': {
            'comment': '自选股分组明细表（多对多关联）',
            'columns': {
                'id': '自增主键',
                'group_id': '所属分组ID（外键）',
                'ts_code': '股票代码',
                'name': '股票名称',
                'sort_order': '组内排序',
                'added_at': '加入时间',
                'notes': '个股备注（筛选逻辑、关注要点等）'
            }
        },
        'focus_stocks': {
            'comment': '重点股跟踪表（老板重点关注）',
            'columns': {
                'id': '自增主键',
                'ts_code': '股票代码',
                'name': '股票名称',
                'notes': '重点跟踪原因/备注',
                'sort_order': '排序',
                'added_at': '加入时间',
                'updated_at': '更新时间'
            }
        },
        'meta_tables': {
            'comment': '表结构元数据说明（SQLite无列注释，用此表记录）',
            'columns': {
                'table_name': '表名（主键）',
                'table_comment': '表说明',
                'column_comment': '字段注释（JSON格式）',
                'created_at': '创建时间',
                'updated_at': '更新时间'
            }
        }
    },
    'tq_daily_*.db': {
        'stock_daily': {
            'comment': '个股日线行情表（按年分文件）',
            'columns': {
                'ts_code': '股票代码（联合主键）',
                'trade_date': '交易日期 YYYYMMDD（联合主键）',
                'open': '开盘价（元）',
                'high': '最高价（元）',
                'low': '最低价（元）',
                'close': '收盘价（元）',
                'pre_close': '昨收价（元）',
                'change': '涨跌额（元）',
                'pct_change': '涨跌幅（%）',
                'vol': '成交量（手）',
                'amount': '成交额（千元）',
                'turnover_rate': '换手率（%）',
                'total_mv': '总市值（万元）',
                'float_mv': '流通市值（万元）',
                'total_share': '总股本（股）',
                'float_share': '流通股本（股）',
                'adj_factor': '复权因子（默认1.0）',
                'limit_up': '涨停价（元）',
                'limit_down': '跌停价（元）',
                'source': '数据来源（tq）',
                'updated_at': '写入时间'
            }
        },
        'index_daily': {
            'comment': '指数日线行情表（按年分文件）',
            'columns': {
                'ts_code': '指数代码（联合主键）',
                'trade_date': '交易日期 YYYYMMDD（联合主键）',
                'name': '指数名称',
                'open': '开盘价',
                'high': '最高价',
                'low': '最低价',
                'close': '收盘价',
                'pre_close': '昨收价',
                'change': '涨跌额',
                'pct_change': '涨跌幅（%）',
                'vol': '成交量',
                'amount': '成交额',
                'source': '数据来源（tq）',
                'updated_at': '写入时间'
            }
        }
    },
    'tq_minline.db': {
        'stock_minline': {
            'comment': '个股分钟线数据表',
            'columns': {
                'ts_code': '股票代码（联合主键）',
                'trade_datetime': '交易时间 YYYYMMDD HH:MM（联合主键）',
                'period': '周期（1m/5m/15m/30m/60m，联合主键）',
                'open': '开盘价',
                'high': '最高价',
                'low': '最低价',
                'close': '收盘价',
                'vol': '成交量（手）',
                'amount': '成交额（元）',
                'source': '数据来源',
                'updated_at': '写入时间'
            }
        },
        'index_minline': {
            'comment': '指数分钟线数据表',
            'columns': {
                'ts_code': '指数代码（联合主键）',
                'trade_datetime': '交易时间（联合主键）',
                'period': '周期（联合主键）',
                'open': '开盘价',
                'high': '最高价',
                'low': '最低价',
                'close': '收盘价',
                'vol': '成交量',
                'amount': '成交额',
                'source': '数据来源',
                'updated_at': '写入时间'
            }
        }
    },
    'tq_financial.db': {
        'stock_fundamentals': {
            'comment': '财务指标表（季度/年度）',
            'columns': {
                'ts_code': '股票代码（联合主键）',
                'end_date': '报告期 YYYYMMDD（联合主键）',
                'ann_date': '公告日期',
                'report_type': '报告类型（Q1/Q2/Q3/Q4/Annual）',
                'basic_eps': '基本每股收益（元）',
                'total_revenue': '营业总收入（元）',
                'net_profit': '净利润（元）',
                'total_assets': '总资产（元）',
                'total_liab': '总负债（元）',
                'roe': '净资产收益率（%）',
                'roa': '总资产收益率（%）',
                'grossprofit_margin': '毛利率（%）',
                'netprofit_margin': '净利率（%）',
                'debt_to_assets': '资产负债率（%）',
                'current_ratio': '流动比率',
                'quick_ratio': '速动比率',
                'total_share': '总股本（股）',
                'float_share': '流通股本（股）',
                'source': '数据来源',
                'updated_at': '写入时间'
            }
        },
        'stock_gbbq': {
            'comment': '股本变迁表（送转、增发、配股等）',
            'columns': {
                'ts_code': '股票代码（联合主键）',
                'change_date': '变动日期 YYYYMMDD（联合主键）',
                'change_type': '变动类型（送转/增发/配股/回购等，联合主键）',
                'total_shares_before': '变动前总股本（股）',
                'total_shares_after': '变动后总股本（股）',
                'float_shares_before': '变动前流通股本（股）',
                'float_shares_after': '变动后流通股本（股）',
                'bonus_ratio': '送股比例（股/10股）',
                'allot_ratio': '配股比例',
                'allot_price': '配股价（元）',
                'source': '数据来源',
                'updated_at': '写入时间'
            }
        },
        'adjust_factor': {
            'comment': '复权因子表',
            'columns': {
                'ts_code': '股票代码（联合主键）',
                'trade_date': '交易日期 YYYYMMDD（联合主键）',
                'adj_factor_qfq': '前复权因子',
                'adj_factor_hfq': '后复权因子',
                'source': '数据来源',
                'updated_at': '写入时间'
            }
        }
    },
    'tq_computed.db': {
        'kline_factors': {
            'comment': 'K线计算因子表（技术指标、统计值）',
            'columns': {
                'ts_code': '股票代码（联合主键）',
                'trade_date': '交易日期 YYYYMMDD（联合主键）',
                'ma5': '5日均线（元）',
                'ma10': '10日均线（元）',
                'ma20': '20日均线（元）',
                'ma60': '60日均线（元）',
                'pct_change_5d': '5日涨跌（最近5个交易日每日涨跌幅平均值，%）',
                'pct_change_10d': '10日涨跌（最近10个交易日每日涨跌幅平均值，%）',
                'pct_change_20d': '20日涨跌（最近20个交易日每日涨跌幅平均值，%）',
                'amplitude_10d': '10日振幅（最近10个交易日每日振幅平均值，%）',
                'amplitude_20d': '20日振幅（最近20个交易日每日振幅平均值，%）',
                'daily_pct_change': '当日涨跌幅 = (当日收盘-前日收盘)/前日收盘×100%',
                'daily_amplitude': '当日振幅 = (最高-最低)/前日收盘×100%',
                'high20_prev': '20日最高价（元）',
                'low10': '10日最低价（元）',
                'high10': '10日最高价（元）',
                'clv': '收盘位置指标 CLV = (2*close-high-low)/(high-low)*100',
                'slope20': '20日价格斜率（回归系数）',
                'ma10_ma20_dist': '10日均线与20日均线距离（%）',
                'price_ma20_dist': '收盘价与20日均线距离（%）',
                'pos_pct_52w': '52周位置百分比（%）',
                'hi52': '52周最高价（元）',
                'lo52': '52周最低价（元）',
                'dd52': '52周最大回撤（%）',
                'overhang_share_60d': '60日顶底抛压指标',
                'hi60': '60日最高价（元）',
                'dist_hi60': '距离60日最高幅度（%）',
                'v5_avg': '5日平均成交量（手）',
                'v20_avg': '20日平均成交量（手）',
                'vr': '量比 = 当日成交量/5日平均成交量',
                'dayv': '当日成交量（手）',
                'v5prev': '5日前成交量（手）',
                'dayr': '日振幅（%）',
                'xmorph': 'X因子-形态（评分用）',
                'xtrend': 'X因子-趋势（评分用）',
                'xpos': 'X因子-位置（评分用）',
                'xchip': 'X因子-筹码（评分用）',
                'vhealth': '量能因子-量能健康度（评分用）',
                'x_total': 'X因子总分',
                'created_at': '计算时间 YYYY-MM-DD HH:MM:SS'
            }
        }
    },
    'tq_internet.db': {
        'stock_basic_extra': {
            'comment': '股票基础信息补充表（互联网数据）',
            'columns': {
                'ts_code': '股票代码（主键）',
                'industry': '所属行业（同花顺/东方财富）',
                'industry_code': '行业代码',
                'concept': '概念板块（JSON数组）',
                'area': '地域（省份）',
                'business_scope': '经营范围',
                'intro': '公司简介',
                'source': '数据来源',
                'updated_at': '更新时间'
            }
        },
        'moneyflow': {
            'comment': '资金流向表（每日）',
            'columns': {
                'ts_code': '股票代码（联合主键）',
                'trade_date': '交易日期 YYYYMMDD（联合主键）',
                'net_mf': '资金净流入（元）',
                'net_amount': '资金净流入（万元）',
                'buy_amount': '买入金额（万元）',
                'sell_amount': '卖出金额（万元）',
                'main_force_inflow': '主力净流入（万元）',
                'retail_inflow': '散户净流入（万元）',
                'source': '数据来源',
                'updated_at': '写入时间'
            }
        },
        'margin': {
            'comment': '融资融券余额表（每日）',
            'columns': {
                'ts_code': '股票代码（联合主键）',
                'trade_date': '交易日期 YYYYMMDD（联合主键）',
                'margin_balance': '融资余额（万元）',
                'margin_short': '融券余额（万元）',
                'margin_total': '两融总额（万元）',
                'source': '数据来源',
                'updated_at': '写入时间'
            }
        },
        'holder': {
            'comment': '股东户数表（定期）',
            'columns': {
                'ts_code': '股票代码（联合主键）',
                'end_date': '截止日期 YYYYMMDD（联合主键）',
                'holder_num': '股东户数（户）',
                'holder_change': '户数变化（户）',
                'holder_change_pct': '户数变化比例（%）',
                'source': '数据来源',
                'updated_at': '写入时间'
            }
        },
        'dividend': {
            'comment': '分红送转记录表',
            'columns': {
                'ts_code': '股票代码（联合主键）',
                'ann_date': '公告日期',
                'record_date': '股权登记日',
                'ex_date': '除权除息日 YYYYMMDD（联合主键）',
                'cash_div': '现金分红（元/股）',
                'share_bonus': '送股比例（股/10股）',
                'allot_ratio': '配股比例',
                'allot_price': '配股价（元）',
                'source': '数据来源',
                'updated_at': '写入时间'
            }
        },
        'announcement': {
            'comment': '公告摘要表',
            'columns': {
                'id': '自增主键',
                'ts_code': '股票代码',
                'ann_date': '公告日期',
                'title': '公告标题',
                'content': '公告内容摘要',
                'ann_type': '公告类型（重大事项/财报/分红等）',
                'source': '数据来源',
                'created_at': '入库时间'
            }
        }
    },
    'trend_research.db': {
        'size_divergence': {
            'comment': '大小盘分化指数趋势变化表（每日）',
            'columns': {
                'trade_date': '交易日期 YYYYMMDD（主键）',
                'zz1000_close': '中证1000收盘价（sh000852）',
                'hs300_close': '沪深300收盘价（sh000300）',
                'divergence': '分化值 = 中证1000收盘 - 沪深300收盘',
                'divergence_pct': '分化值占沪深300比例（%）',
                'ma5_divergence': '分化值5日均线',
                'ma10_divergence': '分化值10日均线',
                'ma20_divergence': '分化值20日均线',
                'slope_5d': '5日趋势斜率（分化值线性回归）',
                'slope_10d': '10日趋势斜率',
                'slope_20d': '20日趋势斜率',
                'signal': '趋势信号（小盘强势/大盘强势/分化收敛/分化加剧）',
                'source': '数据来源（tq）',
                'updated_at': '写入时间'
            }
        }
    }
}

# 获取所有数据库文件
import glob
db_files = glob.glob(os.path.join(D, '*.db'))

for db_file in db_files:
    db_name = os.path.basename(db_file)
    if db_name not in TABLE_META:
        continue
    
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    
    # 确保 meta_tables 表存在
    cur.execute('''
        CREATE TABLE IF NOT EXISTS meta_tables (
            table_name TEXT PRIMARY KEY,
            table_comment TEXT,
            column_comment TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    
    # 获取数据库中实际存在的表
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    actual_tables = [row[0] for row in cur.fetchall()]
    
    # 插入/更新元数据
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for table_name in actual_tables:
        if table_name in TABLE_META[db_name]:
            meta = TABLE_META[db_name][table_name]
            comment = meta['comment']
            columns = meta['columns']
            import json
            col_comment = json.dumps(columns, ensure_ascii=False)
            
            cur.execute('''
                INSERT OR REPLACE INTO meta_tables (table_name, table_comment, column_comment, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (table_name, comment, col_comment, now, now))
            print(f'{db_name}.{table_name}: description added')
    
    conn.commit()
    conn.close()

print('\nAll descriptions updated.')
