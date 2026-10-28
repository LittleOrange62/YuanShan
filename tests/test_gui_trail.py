"""待人工清单（`duanxian/gui_trail.py`）的回归。

守的是用户 2026-10-06 那句条件：自动化动了界面之后，**人要能自己找出哪些没执行、
并照着做一遍**。所以清单的三条硬规矩都有测试：
① 每条非 `done` 都带"人怎么补"；② `done` 不抹掉之前那条没做成的事实；
③ 状态写错不许静默通过。
"""

from __future__ import annotations

import datetime as dt

import pytest

from duanxian import gui_trail as T
from duanxian import paths


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    monkeypatch.setattr(T, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    return tmp_path


def test_状态只有四种写错就抛():
    assert T.STATUSES == ("done", "failed", "need_human", "skipped")
    with pytest.raises(ValueError):
        T.add(step='s1_launch', what='拉起', status='ok')      # 同义词不许混进来


def test_每条非done都自带手动做法():
    """这是这份清单存在的理由。缺了 `manual` 的记录等于让人去猜。"""
    item = T.add(step='s6_day', what='勾日线开始下载', status='need_human',
                 detail='控件定位失败', target='2026-10-06')
    assert '沪深京日线' in item['manual']
    assert item['step'] in T.MANUAL, 'MANUAL 里没登记的步骤不许进清单'
    pend = T.pending()
    assert len(pend) == 1 and pend[0]['status'] == 'need_human'
    assert pend[0]['target'] == '2026-10-06'


def test_未知步骤不许冒领手动做法():
    item = T.add(step='s42_nope', what='编出来的步骤', status='skipped')
    assert '没有登记手动做法' in item['manual']


def test_done不抹掉之前那条没做成的():
    """冲突时要看的是"那一次没成"，后面补成功了不能把它藏起来。"""
    T.add(step='s5_dialog', what='打开盘后数据下载', status='need_human', detail='菜单折叠了')
    T.add(step='s5_dialog', what='打开盘后数据下载', status='done', detail='第二次点中了')
    pend = T.pending()
    assert len(pend) == 1 and pend[0]['status'] == 'need_human'
    assert len(T.items(limit=10)) == 2                  # 全量仍在文件里


def test_同一步同一天只留最后一次处境():
    T.add(step='s7_minute', what='勾分钟线', status='need_human', target='2026-10-06')
    T.add(step='s7_minute', what='勾分钟线', status='failed', target='2026-10-06')
    T.add(step='s7_minute', what='勾分钟线', status='skipped', target='2026-10-07')
    pend = T.pending()
    assert [x['status'] for x in pend] == ['skipped', 'failed']   # 最新在前，同日同键去重


def test_只留最近几天():
    today = dt.date.today().isoformat()
    old = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    T.add(step='s8_finish', what='关窗', status='need_human')
    items = T.items(limit=10, days=3)
    assert len(items) == 1 and items[0]['day'] == today
    # 手动造一条三个月前的，确认 `days` 真的在筛
    import json
    path = T.store_path()
    data = json.load(open(path, encoding='utf-8'))
    data['items'].append({'at': old + 'T16:00:00+08:00', 'day': old, 'step': 's8_finish',
                          'what': '很久以前', 'status': 'need_human', 'manual': 'x'})
    json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
    assert [x['day'] for x in T.items(limit=10, days=3)] == [today]
    assert len(T.items(limit=10, days=400)) == 2


def test_表有上限不无限膨胀():
    for i in range(T.MAX_ITEMS + 60):
        T.add(step='s3_popup', what='清弹窗', status='done', detail=str(i))
    raw = T._load()
    assert len(raw) == T.MAX_ITEMS
    assert raw[-1]['detail'] == str(T.MAX_ITEMS + 59)          # 丢的是最老的


def test_summary_按状态给数():
    T.add(step='s1_launch', what='拉起', status='done')
    T.add(step='s2_login', what='登录', status='need_human')
    s = T.summary()
    assert s['by_status']['done'] == 1 and s['by_status']['need_human'] == 1
    assert s['count'] == 2 and len(s['pending']) == 1
    assert s['path'].endswith('gui_trail.json')


def test_手动做法里不许留参考仓库的路径():
    """这些文字是给人在本机照着点的，命令一律 `-m 模块名`（任务 #42 同一条规矩）。"""
    bad = [k for k, v in T.MANUAL.items() if 'scripts\\\\' in v or 'scripts/' in v]
    assert not bad, bad
    assert all(v for v in T.MANUAL.values())


def test_关客户端这一步有得教而不是一句没有登记():
    """10-13 现场：`close_client` 从没登记进 `MANUAL`，于是待人工清单上那句是
    "这一步没有登记手动做法" —— 而这一步恰恰要点准「退出」（点「取消」就是不关）。"""
    assert "close_client" in T.MANUAL
    text = T.MANUAL["close_client"]
    assert "退出" in text and "重新登录" in text and "取消" in text, "三个按钮要点名，人才不会按错"
    assert "最小化" in text, "最小化的主窗口会无视退出请求，这一条不写人就白点一次 X"


def test_supersedes_只抹同一件事的旧交人工记录():
    """后来的 `done` **默认**不抹前一条（上一条测试钉的就是这条纪律）；
    只有显式声明"这件事后来由程序自己做完了"才抹 —— 否则人会为了补一件已经做完的事
    把刚关掉的客户端再打开（10-13 实测：07:48 交人工、08:09 程序自动答了确认框）。"""
    T.add(step='close_client', what='带外该关通达信但没关掉', status='need_human',
          target='2026-10-13')
    assert len(T.pending()) == 1
    # 不带标记的 done：冲突事实留着（上一条测试的默认口径不许被这次改动悄悄放宽）
    T.add(step='close_client', what='别的日子关掉了', status='done', target='2026-10-12')
    assert len(T.pending()) == 1, "别的 target 不该影响这一条"
    # 带标记且同 (day, step, target)：抹掉，全量记录仍留在文件里
    T.add(step='close_client', what='带外已关闭通达信客户端', status='done',
          target='2026-10-13', supersedes=True)
    assert T.pending() == [], "程序已经做完了就不该再挂着一条让人去补"
    assert len(T.items(limit=10)) == 3, "抹的是清单不是账本，原始记录一条不少"


def test_宿主用到的每一个步骤名都必须登记过手动做法():
    """10-11 与 10-16 两次实测到的形状：`server.py` 用一个没登记的名字写清单，
    屏幕上就念兜底句「这一步没有登记手动做法」—— 那一步恰恰是最需要教人点哪一格的
    （`close_client` 的框上是「退出 / 重新登录 / 取消」三个，`reap_plugin_shell` 是"那个 cmd 窗口敢不敢关"）。
    所以这里**扫调用点**，不靠人记得补。
    """
    import pathlib
    import re

    srv = (pathlib.Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    used = set(re.findall(r'_gui\(\s*["\x27]([a-z_0-9]+)["\x27]', srv))
    assert used, "一个界面动作都没记？那这条测试失去意义，先看调用点是不是改名了"
    unregistered = sorted(used - set(T.MANUAL))
    assert not unregistered, f"这些步骤会让人只看到兜底句：{unregistered}"
    # 反过来：登记了却没人用的键是死文案（改了代码忘了这里会留下）。
    # `s1_launch`…`s9_ingest` 那一族由 `app/tdx_client/steps/*` 自己登记，不经过 server，所以按名字放行。
    dead = sorted(k for k in set(T.MANUAL) - used if not re.match(r"s\d_", k))
    assert not dead, f"MANUAL 里这些键没有任何调用点：{dead}"


def test_交出去的手动做法里不许有裸星号(tmp_path):
    """10-18 补：`TdxTrail.tsx` 用 `{x.manual}` 纯文本渲染，markdown 的星号在那儿
    就是挂在句子中间的两颗星 —— 全站那条「屏幕上不许出现裸 **」同样管到这本账。
    写入侧剥一次，**读出侧也剥一次**（旧行是已经带着星号躺在文件里的）。"""
    item = T.add(step='reap_plugin_shell', what='空壳窗口没收掉，需要人工关',
                 status='need_human', target='2026-10-18')
    assert '**' not in item['manual'], item['manual']
    assert 'app.tdx_client.shells' in item['manual'], '做法要给一条能直接敲的命令'
    assert 'taskkill /IM' in item['manual'] and 'TdxW.exe' in item['manual'], '两条红线必须点名'

    import json
    from pathlib import Path
    p = Path(T.store_path())
    raw = json.loads(p.read_text(encoding='utf-8'))
    raw['items'].insert(0, {'at': '2026-10-16T21:00:00', 'day': '2026-10-16',
                            'step': 's6_day', 'what': '旧行', 'status': 'need_human',
                            'detail': '', 'manual': '勾上 **沪深京日线** 那两格', 'target': None})
    raw['items'].insert(1, {'at': '2026-10-17T23:43:09', 'day': '2026-10-17',
                            'step': 'reap_plugin_shell', 'what': '旧行二', 'status': 'need_human',
                            'detail': '已收 0 个空壳（无）；**这几个发了关闭请求但还在**（pid 3896）',
                            'manual': '', 'target': '2026-10-17'})
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    got = [x for x in T.items(limit=10) if x['step'] == 's6_day' and x['what'] == '旧行']
    assert got and '**' not in got[0]['manual'] and '沪深京日线' in got[0]['manual'], got
    # detail 是各步 reason 的原文，同样直接上屏（10-19 实测那条收空壳的假警报就带着星号）
    two = [x for x in T.items(limit=10) if x['what'] == '旧行二']
    assert two and '**' not in two[0]['detail'] and '这几个发了关闭请求但还在' in two[0]['detail'], two
    pend = T.pending(limit=10, days=30)
    assert all('**' not in (x.get('manual') or '') for x in pend), pend
    assert all('**' not in (x.get('detail') or '') for x in pend), pend


def test_登记表补上以后旧那条兜底句不许继续念():
    """`add()` 写的是**当时的快照**，于是 10-17 那条 `reap_plugin_shell` 在 10-19 登记完做法之后
    仍然念「这一步没有登记手动做法」—— 页面上、doctor 上都一样。登记表才是唯一真源，
    所以读出时按 `step` 现取；只有登记表里真没有这个名字的步骤才留快照/兜底句。"""
    import json
    from pathlib import Path
    p = Path(T.store_path())
    raw = (json.loads(p.read_text(encoding='utf-8')) if p.exists() else {"items": []})
    raw['items'].insert(0, {'at': '2026-10-17T23:45:21', 'day': '2026-10-17',
                            'step': 'reap_plugin_shell', 'what': '旧行：当时没登记做法',
                            'status': 'need_human', 'detail': '',
                            'manual': '这一步没有登记手动做法，见《系统配置工作指南/07-故障恢复手册》',
                            'target': '2026-10-17'})
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    got = [x for x in T.items(limit=10) if x['day'] == '2026-10-17' and x['step'] == 'reap_plugin_shell']
    assert got, T.items(limit=10)
    assert '没有登记手动做法' not in got[0]['manual'], got[0]['manual']
    assert 'app.tdx_client.shells' in got[0]['manual'], got[0]['manual']
    # 登记表里确实没有的名字：兜底句要留着（不许编一个做法出来）
    raw['items'][0]['step'] = 's42_nope'
    raw['items'][0]['manual'] = '这一步没有登记手动做法，见《系统配置工作指南/07-故障恢复手册》'
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    keep = [x for x in T.items(limit=10) if x['step'] == 's42_nope']
    assert keep and '没有登记手动做法' in keep[0]['manual'], keep
