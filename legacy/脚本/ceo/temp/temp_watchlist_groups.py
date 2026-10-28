import sqlite3
from datetime import datetime

D = r'D:\finance\data'
conn = sqlite3.connect(f'{D}/tq_core.db')
cur = conn.cursor()

# 默认24个自选板块分组（参考 TABLE_SCHEMA.md）
groups = [
    (1, '半导体产业链', '半导体材料、设备、设计、制造、封测全产业链'),
    (2, '人工智能/AI', 'AI芯片、大模型、应用端、算力基础设施'),
    (3, '新能源车/智能驾驶', '整车、电池、电机、电控、智能驾驶系统'),
    (4, '光伏/风电/储能', '硅料、硅片、电池片、组件、逆变器、储能'),
    (5, '创新药/医疗器械', '创新药、CXO、高端医疗器械'),
    (6, '消费电子/XR', '手机产业链、VR/AR、可穿戴设备'),
    (7, '云计算/数据中心', 'IDC、服务器、云计算服务、光模块'),
    (8, '军工/航天', '军工电子、航空航天、导弹、卫星'),
    (9, '高端制造/机器人', '工业机器人、数控机床、激光设备'),
    (10, '数字经济/信创', '操作系统、数据库、网络安全、政务IT'),
    (11, '医药商业/流通', '医药零售、流通、冷链'),
    (12, '金融科技', '支付、区块链、数字货币、保险科技'),
    (13, '传媒/游戏/元宇宙', '游戏、影视、数字人、虚拟现实'),
    (14, '食品饮料/白酒', '白酒、调味品、休闲食品、饮料'),
    (15, '房地产/建材', '房地产开发、水泥、玻璃、管材'),
    (16, '有色金属/稀土', '锂、钴、稀土、黄金、铜'),
    (17, '化工/新材料', '氟化工、磷化工、碳纤维、可降解塑料'),
    (18, '农业/种业', '种业、养殖、饲料、农产品加工'),
    (19, '汽车零部件', '底盘、车身、内饰、汽车电子'),
    (20, '家电/家居', '白色家电、厨电、定制家居'),
    (21, '纺织服装', '服装家纺、纺织制造、品牌服饰'),
    (22, '交通运输/物流', '快递、航运、铁路、航空'),
    (23, '环保/公用事业', '水务、固废处理、新能源公用事业'),
    (24, '其他热门概念', '当日市场热点、突发题材')
]

for group_id, name, desc in groups:
    cur.execute('''
        INSERT OR REPLACE INTO watchlist_groups (group_id, name, description, parent_id, sort_order, created_at, updated_at)
        VALUES (?, ?, ?, 0, ?, ?, ?)
    ''', (group_id, name, desc, group_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

# 插入重点股（默认示例，后续可由老板指定）
focus = [
    ('600519.SH', '贵州茅台', '白酒龙头，业绩确定性高'),
    ('000858.SZ', '五粮液', '浓香型白酒龙头'),
    ('300750.SZ', '宁德时代', '动力电池全球龙头'),
    ('002594.SZ', '比亚迪', '新能源汽车整车龙头'),
    ('601012.SH', '隆基绿能', '光伏硅片龙头')
]

for ts_code, name, notes in focus:
    cur.execute('''
        INSERT OR IGNORE INTO focus_stocks (ts_code, name, notes, sort_order, added_at, updated_at)
        VALUES (?, ?, ?, 0, ?, ?)
    ''', (ts_code, name, notes, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

conn.commit()
conn.close()
print(f'Inserted {len(groups)} watchlist groups and {len(focus)} focus stocks.')
