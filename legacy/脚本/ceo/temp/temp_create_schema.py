import sqlite3
import os

D = r'D:\finance\data'
os.makedirs(D, exist_ok=True)

def create_db(filename, tables):
    path = os.path.join(D, filename)
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    for ddl in tables:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    print(f'Created {filename}')

# 1. tq_core.db
core_tables = [
    '''
    CREATE TABLE stock_info (
        ts_code TEXT PRIMARY KEY,
        symbol TEXT,
        name TEXT,
        market TEXT,
        asset_type TEXT,
        list_date TEXT,
        delist_date TEXT,
        industry TEXT,
        area TEXT,
        total_shares REAL,
        float_shares REAL,
        list_status TEXT DEFAULT 'L',
        is_hs TEXT DEFAULT 'N',
        remarks TEXT,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )
    ''',
    '''
    CREATE TABLE index_info (
        ts_code TEXT PRIMARY KEY,
        symbol TEXT,
        name TEXT,
        market TEXT,
        base_date TEXT,
        base_point REAL,
        remarks TEXT,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )
    ''',
    '''
    CREATE TABLE watchlist_groups (
        group_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        parent_id INTEGER DEFAULT 0,
        sort_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )
    ''',
    '''
    CREATE TABLE watchlist_stocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        ts_code TEXT NOT NULL,
        name TEXT,
        sort_order INTEGER DEFAULT 0,
        added_at TEXT DEFAULT (datetime('now','localtime')),
        notes TEXT,
        FOREIGN KEY (group_id) REFERENCES watchlist_groups(group_id) ON DELETE CASCADE
    )
    ''',
    '''
    CREATE TABLE focus_stocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_code TEXT NOT NULL,
        name TEXT,
        notes TEXT,
        sort_order INTEGER DEFAULT 0,
        added_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )
    ''',
    '''
    CREATE TABLE meta_tables (
        table_name TEXT PRIMARY KEY,
        table_comment TEXT,
        column_comment TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )
    ''',
    "CREATE INDEX idx_stock_info_market ON stock_info(market)",
    "CREATE INDEX idx_stock_info_industry ON stock_info(industry)",
    "CREATE INDEX idx_watchlist_stocks_group ON watchlist_stocks(group_id)",
    "CREATE INDEX idx_watchlist_stocks_tscode ON watchlist_stocks(ts_code)",
    "CREATE INDEX idx_focus_stocks_tscode ON focus_stocks(ts_code)"
]
create_db('tq_core.db', core_tables)

# 2. tq_daily_2026.db (latest year)
daily_tables = [
    '''
    CREATE TABLE stock_daily (
        ts_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        pre_close REAL,
        change REAL,
        pct_change REAL,
        vol REAL,
        amount REAL,
        turnover_rate REAL,
        total_mv REAL,
        float_mv REAL,
        total_share REAL,
        float_share REAL,
        adj_factor REAL DEFAULT 1.0,
        limit_up REAL,
        limit_down REAL,
        source TEXT DEFAULT 'tq',
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, trade_date)
    )
    ''',
    '''
    CREATE TABLE index_daily (
        ts_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        name TEXT,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        pre_close REAL,
        change REAL,
        pct_change REAL,
        vol REAL,
        amount REAL,
        source TEXT DEFAULT 'tq',
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, trade_date)
    )
    ''',
    "CREATE INDEX idx_stock_daily_tsdate ON stock_daily(ts_code, trade_date)",
    "CREATE INDEX idx_stock_daily_date ON stock_daily(trade_date)",
    "CREATE INDEX idx_index_daily_tsdate ON index_daily(ts_code, trade_date)"
]
create_db('tq_daily_2026.db', daily_tables)

# 3. tq_minline.db
min_tables = [
    '''
    CREATE TABLE stock_minline (
        ts_code TEXT NOT NULL,
        trade_datetime TEXT NOT NULL,
        period TEXT NOT NULL DEFAULT '1m',
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        vol REAL,
        amount REAL,
        source TEXT DEFAULT 'tq',
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, trade_datetime, period)
    )
    ''',
    '''
    CREATE TABLE index_minline (
        ts_code TEXT NOT NULL,
        trade_datetime TEXT NOT NULL,
        period TEXT NOT NULL DEFAULT '1m',
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        vol REAL,
        amount REAL,
        source TEXT DEFAULT 'tq',
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, trade_datetime, period)
    )
    ''',
    "CREATE INDEX idx_stock_minline_tsdt ON stock_minline(ts_code, trade_datetime)",
    "CREATE INDEX idx_index_minline_tsdt ON index_minline(ts_code, trade_datetime)"
]
create_db('tq_minline.db', min_tables)

# 4. tq_financial.db
fin_tables = [
    '''
    CREATE TABLE stock_fundamentals (
        ts_code TEXT NOT NULL,
        end_date TEXT NOT NULL,
        ann_date TEXT,
        report_type TEXT,
        basic_eps REAL,
        total_revenue REAL,
        net_profit REAL,
        total_assets REAL,
        total_liab REAL,
        roe REAL,
        roa REAL,
        grossprofit_margin REAL,
        netprofit_margin REAL,
        debt_to_assets REAL,
        current_ratio REAL,
        quick_ratio REAL,
        total_share REAL,
        float_share REAL,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, end_date, report_type)
    )
    ''',
    '''
    CREATE TABLE stock_gbbq (
        ts_code TEXT NOT NULL,
        change_date TEXT NOT NULL,
        change_type TEXT NOT NULL,
        total_shares_before REAL,
        total_shares_after REAL,
        float_shares_before REAL,
        float_shares_after REAL,
        bonus_ratio REAL,
        allot_ratio REAL,
        allot_price REAL,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, change_date, change_type)
    )
    ''',
    '''
    CREATE TABLE adjust_factor (
        ts_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        adj_factor_qfq REAL,
        adj_factor_hfq REAL,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, trade_date)
    )
    ''',
    "CREATE INDEX idx_fundamentals_tsdate ON stock_fundamentals(ts_code, end_date)",
    "CREATE INDEX idx_gbbq_tsdate ON stock_gbbq(ts_code, change_date)"
]
create_db('tq_financial.db', fin_tables)

# 5. tq_computed.db
comp_tables = [
    '''
    CREATE TABLE kline_factors (
        ts_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        ma5 REAL,
        ma10 REAL,
        ma20 REAL,
        ma60 REAL,
        pct_change_5d REAL,
        pct_change_10d REAL,
        pct_change_20d REAL,
        amplitude_10d REAL,
        amplitude_20d REAL,
        daily_pct_change REAL,
        daily_amplitude REAL,
        high20_prev REAL,
        low10 REAL,
        high10 REAL,
        clv REAL,
        slope20 REAL,
        ma10_ma20_dist REAL,
        price_ma20_dist REAL,
        pos_pct_52w REAL,
        hi52 REAL,
        lo52 REAL,
        dd52 REAL,
        overhang_share_60d REAL,
        hi60 REAL,
        dist_hi60 REAL,
        v5_avg REAL,
        v20_avg REAL,
        vr REAL,
        dayv REAL,
        v5prev REAL,
        dayr REAL,
        xmorph REAL,
        xtrend REAL,
        xpos REAL,
        xchip REAL,
        vhealth REAL,
        x_total REAL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, trade_date)
    )
    ''',
    "CREATE INDEX idx_kline_factors_tsdate ON kline_factors(ts_code, trade_date)"
]
create_db('tq_computed.db', comp_tables)

# 6. tq_internet.db
inet_tables = [
    '''
    CREATE TABLE stock_basic_extra (
        ts_code TEXT PRIMARY KEY,
        industry TEXT,
        industry_code TEXT,
        concept TEXT,
        area TEXT,
        business_scope TEXT,
        intro TEXT,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )
    ''',
    '''
    CREATE TABLE moneyflow (
        ts_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        net_mf REAL,
        net_amount REAL,
        buy_amount REAL,
        sell_amount REAL,
        main_force_inflow REAL,
        retail_inflow REAL,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, trade_date)
    )
    ''',
    '''
    CREATE TABLE margin (
        ts_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        margin_balance REAL,
        margin_short REAL,
        margin_total REAL,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, trade_date)
    )
    ''',
    '''
    CREATE TABLE holder (
        ts_code TEXT NOT NULL,
        end_date TEXT NOT NULL,
        holder_num INTEGER,
        holder_change REAL,
        holder_change_pct REAL,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, end_date)
    )
    ''',
    '''
    CREATE TABLE dividend (
        ts_code TEXT NOT NULL,
        ann_date TEXT,
        record_date TEXT,
        ex_date TEXT NOT NULL,
        cash_div REAL,
        share_bonus REAL,
        allot_ratio REAL,
        allot_price REAL,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (ts_code, ex_date)
    )
    ''',
    '''
    CREATE TABLE announcement (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_code TEXT NOT NULL,
        ann_date TEXT,
        title TEXT,
        content TEXT,
        ann_type TEXT,
        source TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    ''',
    "CREATE INDEX idx_moneyflow_tsdate ON moneyflow(ts_code, trade_date)",
    "CREATE INDEX idx_margin_tsdate ON margin(ts_code, trade_date)",
    "CREATE INDEX idx_holder_tsdate ON holder(ts_code, end_date)",
    "CREATE INDEX idx_dividend_tsdate ON dividend(ts_code, ex_date)",
    "CREATE INDEX idx_announcement_tsdate ON announcement(ts_code, ann_date)"
]
create_db('tq_internet.db', inet_tables)

# 7. trend_research.db
trend_tables = [
    '''
    CREATE TABLE size_divergence (
        trade_date TEXT PRIMARY KEY,
        zz1000_close REAL,
        hs300_close REAL,
        divergence REAL,
        divergence_pct REAL,
        ma5_divergence REAL,
        ma10_divergence REAL,
        ma20_divergence REAL,
        slope_5d REAL,
        slope_10d REAL,
        slope_20d REAL,
        signal TEXT,
        source TEXT DEFAULT 'tq',
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )
    '''
]
create_db('trend_research.db', trend_tables)

print('All schemas created.')
