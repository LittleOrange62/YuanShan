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
    why = tasks.validate_patch({'heat_times': '10:05,25:99'}, tasks.DEFAULTS)
    assert why and '25:99' in why and '整条没写入' in why


def test_时点清空成空表算非法():
    """`heat_times=",,"` 会被切成零个时点：写下去就是一个每天到点、什么都不播的任务。"""
    why = tasks.validate_patch({'heat_times': ',,'}, tasks.DEFAULTS)
    assert why and '至少要留一个时点' in why


def test_开着却没有时点时不生成任务定义():
    """读面（`jobs.settings`）没有形状校验，脏值能进到配置里；到生成 XML 这一步必须抛，
    而不是插出一份 `<Triggers></Triggers>` 让 PowerShell 回一句看不懂的话。"""
    cfg = {**tasks.DEFAULTS, 'heat_times': ''}
    with pytest.raises(RuntimeError, match='时点'):
        tasks.build_xml('heat', cfg)


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
    cfg = {**tasks.DEFAULTS, 'heat_times': '10:05,10:55,14:18,14:45',
           'auction_times': '09:25:30'}
    heat = [b.text for b in ET.fromstring(tasks.build_xml('heat', cfg))
            .iter(f'{NS}StartBoundary')]
    assert len(heat) == 4 and heat[0].endswith('T10:05:00') and heat[-1].endswith('T14:45:00')
    # 竞价那一档要能到秒
    assert 'T09:25:30<' in tasks.build_xml('auction', cfg)


def test_xml_重复间隔与时长取自配置():
    cfg = {**tasks.DEFAULTS, 'watcher_interval_min': 3, 'watcher_duration_h': 7}
    xml = tasks.build_xml('watcher', cfg)
    assert '<Interval>PT3M</Interval>' in xml and '<Duration>PT7H</Duration>' in xml


def test_任务参数里不写渠道():
    """渠道由作业每轮现读配置。写进任务参数就会出现"配置说 A、任务参数说 B"。"""
    assert '--channel' not in tasks.task_args('watcher', tasks.DEFAULTS)
    assert tasks.task_args('watcher', tasks.DEFAULTS) == \
        '-X utf8 -m ingest.jobs.zt_watch --once --send'


def test_对账只看钟点不看注册那天():
    """`StartBoundary` 的日期是注册那天，每日触发器靠它推下一场；
    拿整串比的话四条会永远显示"时点不一致"，一条常红的对账等于没有对账。"""
    cfg = {**tasks.DEFAULTS, 'heat_times': '10:05'}
    want = tasks.want_boundaries('heat', cfg, day=dt.date(2026, 11, 2))
    got = ['2026-10-27T10:05:00']
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
    calls: list[str] = []
    monkeypatch.setattr(tasks, 'run_powershell',
                        lambda script, **kw: calls.append(script) or (0, 'Ready', ''))
    monkeypatch.setattr(tasks, 'XML_DIR', tmp_path)
    row = tasks.register_one('noon', tasks.DEFAULTS, want_on=True)
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
    assert '不是这四条之一' in capsys.readouterr().out
    assert tasks.main(['--set', 'heat_times=']) == 2
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
    ]
    led = store.fold_ledger(rows)
    assert led['watcher'] == {**led['watcher'], 'rounds': 51, 'sent': 17, 'failed': 0,
                              'skipped': 22, 'notes': []}
    assert led['watcher']['first_at'] == '09:15' and led['watcher']['last_at'] == '15:15'
    # 失败的那一条要看得见"为什么"，而缺数降级单独成一条注记，不许并进 rounds/sent
    assert led['auction']['sent'] == 1 and led['auction']['failed'] == 1
    assert led['auction']['last_err'] == '没有可用渠道，未发送'
    assert [n['what'] for n in led['auction']['notes']] == ['auction_source_fallback']


def test_默认值与出厂节奏一致():
    """这四个默认值就是原 `scripts/ops/setup_intraday_*.ps1` 里写死的那套。
    哪天改动默认值而没改文档，界面上"出厂值"那句话就开始说谎。"""
    assert tasks.DEFAULTS['watcher_start'] == '09:15'
    assert (tasks.DEFAULTS['watcher_interval_min'], tasks.DEFAULTS['watcher_duration_h']) == (5, 6)
    assert tasks.DEFAULTS['auction_times'] == '09:25:30'
    assert tasks.DEFAULTS['heat_times'] == '10:05,10:55,14:18'
    assert tasks.DEFAULTS['noon_times'] == '12:10'
    assert tasks.DEFAULTS['channels'] == 'feishu-main'
