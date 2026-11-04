# -*- coding: utf-8 -*-
"""「盘中播报」配置面与计划任务面（`app/intraday/tasks.py`）的钉住用例。

这里**一条都不会真动任务计划程序**：起 PowerShell 的唯一出口 `run_powershell`
在本文件里要么被 monkeypatch，要么由它自己的 pytest 硬挡闸直接抛。
最后那一条（`test_测试进程里硬挡真注册`）就是这道闸自己的取证 —— 闸坏掉的那天，
跑一次测试就会往机器上长出四个每日任务，那种事撤不回来。
"""
from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET

import pytest

from app.intraday import store, tasks

NS = '{http://schemas.microsoft.com/windows/2004/02/mit/task}'


# --------------------------------------------------------------------------- 校验（写面）

def test_时点写坏整条不写():
    why = tasks.validate_patch({'push_times': '09:35,25:99'}, tasks.DEFAULTS)
    assert why and '25:99' in why and '整条没写入' in why


def test_时点清空成空表算非法():
    """`push_times=",,"` 会被切成零个时点：写下去就是一个每天到点、什么都不播的任务。"""
    why = tasks.validate_patch({'push_times': ',,'}, tasks.DEFAULTS)
    assert why and '至少要留一个时点' in why


def test_旧的_heat_times_键已经不存在():
    """热度跟推送时点走（+10 秒），不再留第二份可改的时点：留着就会出现
    "改了推送点、忘了改热度"，而屏上两格都各自像是配好了。"""
    why = tasks.validate_patch({'heat_times': '10:05'}, tasks.DEFAULTS)
    assert why and 'heat_times' in why and 'push_times' in why


def test_开着却没有时点时不生成任务定义():
    """读面（`jobs.settings`）没有形状校验，脏值能进到配置里；到生成 XML 这一步必须抛，
    而不是插出一份 `<Triggers></Triggers>` 让 PowerShell 回一句看不懂的话。
    推导出来的那两路（变化量/热度）同样要抛，且报错要点名真正的来源键 `push_times`。"""
    cfg = {**tasks.DEFAULTS, 'push_times': ''}
    for key in ('delta', 'heat'):
        with pytest.raises(RuntimeError, match='push_times'):
            tasks.build_xml(key, cfg)
    with pytest.raises(RuntimeError, match='noon_times'):
        tasks.build_xml('noon', {**tasks.DEFAULTS, 'noon_times': ''})


def test_间隔越界拒掉():
    assert tasks.validate_patch({'watcher_interval_min': 0}, tasks.DEFAULTS)
    assert tasks.validate_patch({'watcher_interval_min': 31}, tasks.DEFAULTS)
    assert tasks.validate_patch({'watcher_duration_h': 13}, tasks.DEFAULTS)
    assert tasks.validate_patch({'watcher_interval_min': 5}, tasks.DEFAULTS) is None


def test_不在册的键直接点名():
    why = tasks.validate_patch({'heat_start': '10:05'}, tasks.DEFAULTS)
    assert why and '不在册' in why


def test_渠道必须是已登记的渠道(monkeypatch):
    """拼错的渠道键不会报错，只会让 `send_to` 回一句"没有可用渠道" —— 那正是"存下了、
    永远发不出去"，必须在写面就挡住。"""
    import notifyconfig as NC

    monkeypatch.setattr(NC, 'load', lambda: {'channels': [{'key': 'feishu-main'},
                                                           {'key': 'dingtalk-main'}]})
    assert tasks.validate_patch({'channels': 'feishu-main'}, tasks.DEFAULTS) is None
    assert tasks.validate_patch({'channels': 'feishu-main,ghost'}, tasks.DEFAULTS)
    assert tasks.validate_patch({'channels': 'feishu-main,dingtalk-main'},
                                tasks.DEFAULTS) is None


def test_渠道名单读不出来时不许当都不存在(monkeypatch):
    """判据探测挂了不能兜成"这条渠道不存在"，那会把观测层故障说成业务事实。"""
    import notifyconfig as NC

    def boom():
        raise RuntimeError('配置盘读不到')

    monkeypatch.setattr(NC, 'load', boom)
    why = tasks.validate_patch({'channels': 'feishu-main'}, tasks.DEFAULTS)
    assert why and '读不出来' in why


# --------------------------------------------------------------------------- 任务定义（XML）

def test_xml_是合法任务定义():
    """真解析一遍。闭合标签打错这种事故不会让 Python 报错，只会让注册当场失败 ——
    而失败信息在 PowerShell 那侧，界面上只剩一句"注册失败"。"""
    for key in tasks.SPEC:
        xml = tasks.build_xml(key, tasks.DEFAULTS)
        root = ET.fromstring(xml)
        assert root.get('version') == '1.3'
        assert root.tag == f'{NS}Task'
        boundaries = [b.text for b in root.iter(f'{NS}StartBoundary')]
        assert boundaries == tasks.want_boundaries(key, tasks.DEFAULTS)


def test_xml_逐条对应配置里的时点():
    """六点→变化量按原样、→热度各自 +10 秒（同一格时点的两种写法，改一处两处跟着变）。"""
    cfg = {**tasks.DEFAULTS, 'push_times': '10:05,10:55,14:18,14:45',
           'auction_times': '09:25:30'}
    got = lambda key: [b.text for b in ET.fromstring(tasks.build_xml(key, cfg))
                       .iter(f'{NS}StartBoundary')]
    assert got('delta') == tasks.want_boundaries('delta', cfg)
    assert [t[-8:] for t in got('heat')] == ['10:05:10', '10:55:10', '14:18:10', '14:45:10']
    assert got('heat') == tasks.want_boundaries('heat', cfg)
    # 竞价那一档要能到秒
    assert 'T09:25:30<' in tasks.build_xml('auction', cfg)


def test_xml_重复间隔与时长取自配置():
    cfg = {**tasks.DEFAULTS, 'watcher_interval_min': 3, 'watcher_duration_h': 7}
    xml = tasks.build_xml('watcher', cfg)
    assert '<Interval>PT3M</Interval>' in xml and '<Duration>PT7H</Duration>' in xml


def test_任务参数里不写渠道():
    """渠道由作业每轮现读配置。写进任务参数就会出现"配置说 A、任务参数说 B"。"""
    assert '--channel' not in tasks.task_args('watcher', tasks.DEFAULTS)
    # 采集那一炉只落库：老写法 `--once --send` 是"边取边播"，11-03 起发送归六点那一炉
    assert tasks.task_args('watcher', tasks.DEFAULTS) == '-X utf8 -m ingest.jobs.push.t01_zt_watch --once'
    assert tasks.task_args('delta', tasks.DEFAULTS) == '-X utf8 -m ingest.jobs.push.t03_zt_delta --send'
    # 分层在配置面上也要有一格说法：`mode=record` 的那一路"今天发 0 条"是本来就不发，不是坏了
    assert tasks.SPEC['watcher']['mode'] == 'record'
    assert tasks.SPEC['delta'].get('mode', 'push') == 'push'


def test_对账只看钟点不看注册那天():
    """`StartBoundary` 的日期是注册那天，每日触发器靠它推下一场；
    拿整串比的话这几条会永远显示"时点不一致"，一条常红的对账等于没有对账。"""
    cfg = {**tasks.DEFAULTS, 'push_times': '10:05'}
    want = tasks.want_boundaries('heat', cfg, day=dt.date(2026, 11, 2))
    got = ['2026-10-27T10:05:10']
    assert tasks._clock(want) == tasks._clock(got)


# --------------------------------------------------------------------------- 落地（不真动机器）

def test_dry_run_只落_xml_不起_powershell(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(tasks, 'run_powershell',
                        lambda script, **kw: calls.append(script) or (0, 'Ready', ''))
    monkeypatch.setattr(tasks, 'XML_DIR', tmp_path)
    row = tasks.register_one('heat', tasks.DEFAULTS, want_on=True, dry_run=True)
    assert row['ok'] is True and calls == []
    assert (tmp_path / 'YuanShan-HeatReport.xml').is_file()


def test_执行体不在仓库就拒注册(monkeypatch, tmp_path):
    """用一条**故意指向不存在的执行体**的假路数来钉这条闸，不拿真四条当样本 ——
    午间那条已经搬进仓库了，拿它当"没搬进来"的样本，明年就是一句假绿。"""
    calls: list[str] = []
    monkeypatch.setattr(tasks, 'run_powershell',
                        lambda script, **kw: calls.append(script) or (0, 'Ready', ''))
    monkeypatch.setattr(tasks, 'XML_DIR', tmp_path)
    ghost = {'label': '幽灵路', 'task_name': 'YuanShan-Ghost', 'module': 'ingest.jobs.ghost_job',
             'base_args': '--send', 'shape': 'times', 'limit': 'PT15M',
             'retry_interval': 'PT2M', 'retry_count': 2}
    monkeypatch.setitem(tasks.SPEC, 'ghost', ghost)
    row = tasks.register_one('ghost', tasks.DEFAULTS, want_on=True)
    assert row['action'] == 'refused' and row['ok'] is False
    assert calls == []                      # 不注册一个到点必然空跑的任务
    assert list(tmp_path.iterdir()) == []


def test_关掉的那一路是注销任务并且回体要认(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(tasks, 'run_powershell',
                        lambda script, **kw: seen.append(script) or (0, 'GONE', ''))
    row = tasks.register_one('heat', tasks.DEFAULTS, want_on=False)
    assert row['action'] == 'removed' and row['ok'] is True
    assert 'Unregister-ScheduledTask' in seen[0]


def test_回体说还在就不能报成已注销(monkeypatch):
    monkeypatch.setattr(tasks, 'run_powershell',
                        lambda script, **kw: (0, 'STILL-THERE', ''))
    row = tasks.register_one('heat', tasks.DEFAULTS, want_on=False)
    assert row['ok'] is False and '注销没成功' in row['detail']


def test_注册失败要带_powershell_的回执(monkeypatch, tmp_path):
    monkeypatch.setattr(tasks, 'XML_DIR', tmp_path)
    monkeypatch.setattr(tasks, 'run_powershell',
                        lambda script, **kw: (1, '', 'XML 无法解析'))
    row = tasks.register_one('heat', tasks.DEFAULTS, want_on=True)
    assert row['ok'] is False and 'XML 无法解析' in row['detail']


def test_测试进程里硬挡真注册():
    """这道闸自己的取证：闸坏了，跑测试就会往机器上真注册每日任务。"""
    with pytest.raises(RuntimeError, match='pytest'):
        tasks.run_powershell("Get-ScheduledTask | Out-Null")


def test_命令行先拒非法输入再动任何东西(capsys):
    """`--only ghost` 与 `--set K=`（空值＝删键回默认）都不该走到写配置那一步 ——
    静悄悄改口径比报错难查得多。"""
    assert tasks.main(['--only', 'ghost', '--apply']) == 2
    assert '不是在册的某一路' in capsys.readouterr().out
    assert tasks.main(['--set', 'push_times=']) == 2
    assert '留空不写' in capsys.readouterr().out


def test_任务状态读一次缓存十五秒但保存后立刻作废(monkeypatch):
    """面板每开一次都要起一次 PowerShell（实测 4~5 秒），所以留短缓存；
    但刚保存完还显示上一个状态，就等于让界面说谎。"""
    calls: list[str] = []

    def fake(script, **kw):
        calls.append(script)
        return (0, '"Name","State","Last","Next","Result","Args","Trig"\r\n'
                   '"YuanShan-HeatReport","Ready","","","","","2026-11-02T10:05:00"', '')

    monkeypatch.setattr(tasks, 'run_powershell', fake)
    monkeypatch.setattr(tasks, 'load_cfg', lambda: (dict(tasks.DEFAULTS), []))
    reads = lambda: [c for c in calls if 'ConvertTo-Csv' in c]      # 只数"查状态"那类
    tasks._invalidate()
    tasks.plan()
    tasks.plan()
    assert len(reads()) == 1, '十五秒内第二次读不该再起一次 PowerShell'
    tasks.register_one('heat', tasks.DEFAULTS, want_on=True)   # 动过机器
    tasks.plan()
    assert len(reads()) == 2, '保存之后必须重新查，不许拿旧状态回给人看'


# --------------------------------------------------------------------------- 渠道读法与留痕

def test_渠道不给就读配置(monkeypatch):
    monkeypatch.setattr(tasks, 'load_cfg',
                        lambda: ({**tasks.DEFAULTS, 'channels': 'feishu-main,dingtalk-main'}, []))
    assert tasks.send_channels(None) == ['feishu-main', 'dingtalk-main']
    assert tasks.send_channels('feishu-main') == ['feishu-main']
    assert tasks.send_channels('') == ['feishu-main', 'dingtalk-main']


def test_配置里渠道为空要抛不许静默换成默认(monkeypatch):
    monkeypatch.setattr(tasks, 'load_cfg', lambda: ({**tasks.DEFAULTS, 'channels': ''}, []))
    with pytest.raises(RuntimeError, match='渠道是空的'):
        tasks.send_channels(None)


def test_渠道串切不出名字也抛():
    with pytest.raises(RuntimeError, match='渠道为空'):
        tasks.send_channels(' , ')


def test_留痕分类把跳针与缺数分开():
    rows = [
        {'task': 'watcher', 'what': 'digest', 'n': 51, 'first_at': '09:15', 'last_at': '15:00',
         'all_ok': None, 'last_err': None},
        {'task': 'watcher', 'what': 'sent', 'n': 17, 'first_at': '09:25', 'last_at': '13:55',
         'all_ok': True, 'last_err': None},
        {'task': 'watcher', 'what': 'skipped_offsession', 'n': 22, 'first_at': '09:15',
         'last_at': '15:15', 'all_ok': None, 'last_err': '不在交易时段'},
        {'task': 'auction', 'what': 'sent', 'n': 1, 'first_at': '09:25', 'last_at': '09:25',
         'all_ok': False, 'last_err': '没有可用渠道，未发送'},
        {'task': 'auction', 'what': 'auction_source_fallback', 'n': 1, 'first_at': '09:25',
         'last_at': '09:25', 'all_ok': None, 'last_err': None},
        {'task': 'delta', 'what': 'delta', 'n': 6, 'first_at': '09:35', 'last_at': '14:00',
         'all_ok': None, 'last_err': None},
        {'task': 'delta', 'what': 'sent', 'n': 6, 'first_at': '09:35', 'last_at': '14:00',
         'all_ok': True, 'last_err': None},
        {'task': 'delta', 'what': 'delta_no_data', 'n': 1, 'first_at': '11:30', 'last_at': '11:30',
         'all_ok': None, 'last_err': '库里当天零行'},
    ]
    led = store.fold_ledger(rows)
    assert led['watcher'] == {**led['watcher'], 'rounds': 51, 'sent': 17, 'failed': 0,
                              'skipped': 22, 'notes': []}
    # 六点那一炉：`what='delta'` 是"这一针跑了"，不落在 rounds 里的话屏上就是"6 针 0 条"
    assert led['delta']['rounds'] == 6 and led['delta']['sent'] == 6
    assert [n['what'] for n in led['delta']['notes']] == ['delta_no_data']
    assert led['watcher']['first_at'] == '09:15' and led['watcher']['last_at'] == '15:15'
    # 失败的那一条要看得见"为什么"，而缺数降级单独成一条注记，不许并进 rounds/sent
    assert led['auction']['sent'] == 1 and led['auction']['failed'] == 1
    assert led['auction']['last_err'] == '没有可用渠道，未发送'
    assert [n['what'] for n in led['auction']['notes']] == ['auction_source_fallback']


def test_交易日判据只有一份_日历读不出来时按周末兜底(monkeypatch):
    """调度器是**每日**触发（`DaysInterval=1`），休市那天全靠脚本内这一道闸。
    11-03 抓到 `heat_report` 只有时段闸（`in_session` 周六 09:35 也是真）⇒ 周末会起一炉拿空池播假读数；
    而 `日历 is False` 那种写法在日历本身读不出来（None）的周末照样放行。少播一条看得见，
    周末推一条"今天 0 家涨停"是没人会怀疑的假读数。"""
    from ingest.jobs.push import t01_zt_watch as Z
    monkeypatch.setattr(Z, 'is_trade_day', lambda now: False)
    assert Z.is_trading_day(dt.datetime(2026, 11, 4, 9, 35)) is False
    monkeypatch.setattr(Z, 'is_trade_day', lambda now: None)
    assert Z.is_trading_day(dt.datetime(2026, 11, 7, 9, 35)) is False      # 周六：读不出来就不播
    assert Z.is_trading_day(dt.datetime(2026, 11, 4, 9, 35)) is True       # 周二：读不出来仍放行
    monkeypatch.setattr(Z, 'is_trade_day', lambda now: True)
    assert Z.is_trading_day(dt.datetime(2026, 11, 4, 9, 35)) is True


def test_九路都走那一份交易日判据():
    """守卫：谁再在自己脚本里写 `is_trade_day(...) is False`／自己判周末，就又是两份口径。
    读的是 `SPEC` 里那些模块对应的文件 —— 挪目录之后按名去 `ingest/jobs/` 找就已经找不到了。"""
    import inspect
    import pathlib

    from ingest.jobs.push import t01_zt_watch as zt_watch
    root = pathlib.Path(__file__).resolve().parent.parent
    for key, spec in tasks.SPEC.items():
        path = root / (spec['module'].replace('.', '/') + '.py')
        assert path.is_file(), f'{key} 的执行体文件不在（{path}）'
        src = path.read_text(encoding='utf-8')
        assert 'is_trading_day(' in src, f'{path.name} 没走统一判据'
        if key != 'watcher':
            assert 'weekday() > 4' not in src, f'{path.name} 自己兜了一份周末判断'
    assert 'weekday() > 4' in inspect.getsource(zt_watch.is_trading_day)
    assert 'weekday()' not in inspect.getsource(zt_watch.one_round)


def test_默认值与出厂节奏一致():
    """这几个默认值就是界面上"出厂值"那句话读的东西（时点族 11-03 由用户重定：
    事件差不再实时播，改成六点变化量 + 热度跟同一格 +10 秒）。
    哪天改动默认值而没改文档，屏上那句话就开始说谎。"""
    assert tasks.DEFAULTS['watcher_start'] == '09:15'
    assert (tasks.DEFAULTS['watcher_interval_min'], tasks.DEFAULTS['watcher_duration_h']) == (5, 6)
    assert tasks.DEFAULTS['auction_times'] == '09:25:30'
    assert tasks.DEFAULTS['push_times'] == '09:35,09:45,10:10,10:50,11:25,14:00'
    assert tasks.DEFAULTS['noon_times'] == '12:10'
    assert tasks.DEFAULTS['channels'] == 'feishu-main'


def test_界面行序等于文件名里的序号():
    """`ingest/jobs/push/tNN_*.py` 的序号就是界面上从上往下第几行 —— 两边一旦错开，
    "改第 4 行去找哪个文件"就又要靠猜。同时验执行体真的在（改名最容易漏掉引用）。"""
    for i, (key, spec) in enumerate(tasks.SPEC.items(), 1):
        assert spec['module'].startswith(f'ingest.jobs.push.t{i:02d}_'), \
            f'第 {i} 行 {key} 的文件序号不是 t{i:02d}：{spec["module"]}'
        assert tasks.module_exists(spec['module']), f'{key} 的执行体找不到（{spec["module"]}）'


def test_只落库那一路在屏上要指出去发送归谁(monkeypatch):
    """`mode='record'` 的行"今天发 0 条"是本来就不发 —— 那句解释由后端给（名字与在册表同源），
    前端自己编一个就会在某天改名之后变成假话。"""
    monkeypatch.setattr(tasks, 'run_powershell',
                        lambda script, **kw: (0, '"Name","State","Last","Next","Result","Args","Trig"\r\n'
                                                 '"YuanShan-IntradayZtWatch","Ready","","","","",'
                                                 '"2026-11-03T09:15:00"', ''))
    monkeypatch.setattr(tasks, 'load_cfg', lambda: (dict(tasks.DEFAULTS), []))
    tasks._invalidate()
    rows = {r['key']: r for r in tasks.plan()['tasks']}
    assert rows['watcher']['mode'] == 'record' and rows['watcher']['push_label'] == '盘中涨跌停变化量'
    assert rows['sysentry_fill']['push_label'] == tasks.SPEC['sysentry']['short']
    assert rows['delta']['mode'] == 'push' and rows['delta']['push_label'] is None
    # 谁给输入框、谁只给开关，由后端这一格说死（前端不另起一套"谁算推导"的判断）
    assert rows['delta']['times_field'] == 'push_times'
    assert rows['heat']['times_field'] is None
    assert rows['sysentry']['times_field'] == 'sysentry_times'
    assert rows['watcher']['times_field'] is None
