"""每日备份"这一轮该做什么"（`app/ops/backup.py::kind_for` / `run_once`）。

2026-10-12 接调度那晚钉下的四条，都是当时真踩过的形状：

1. **档由日期定，且只住一处** —— 宿主循环与 `scripts/jobs.py run-now backup` 必须读同一个判据，
   否则会出现"页面手动跑的是 daily、夜里循环跑的是 weekly"两份产物互相顶。
2. **`verify.ok is None` 不是失败**：weekly 那一档按设计不做恢复验证（全库 dump 太大），
   把"没验"当成"验不过"会让每周那一档永远记成红。
3. **背不成必须有一格账和一个名字**（异常类型 + 原因）：线程默默消失就是"失败了没人知道"。
4. **台账只有一份**：记账键固定是 `backup`，detail 里要能读出档、产物、大小与验证结论 ——
   `manage.py doctor` 的备份项与 `/api/jobs` 读的就是这一格。
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from app.ops import backup

ROOT = Path(__file__).resolve().parents[1]


def test_档由日期定每月一号是全库那一档():
    # 2026-10-01 周四、10-12 周一、10-17 周六、10-18 周日
    assert backup.kind_for(dt.date(2026, 10, 1)) == 'monthly', '每月 1 号跑最兜底的那一档（含 basebackup）'
    assert backup.kind_for(dt.date(2026, 11, 1)) == 'monthly', '判据只看"是不是 1 号"，与星期几无关'
    assert backup.kind_for(dt.date(2026, 10, 17)) == 'weekly'
    assert backup.kind_for(dt.date(2026, 10, 18)) == 'weekly'
    for d in (dt.date(2026, 10, 12), dt.date(2026, 10, 15), dt.date(2026, 10, 16)):
        assert backup.kind_for(d) == 'daily', '工作日只背核心 schema：全库天天跑会把夜间窗口吃掉'


def test_生产给的是字符串那一种也必须判得出来():
    """**这一条不许打桩** —— 它钉的就是"喂进来的到底是哪种类型"。

    2026-10-14 20:13 抓到：常驻备份循环递进来的是 `util.china_today()` 的返回值
    （字符串 `"2026-10-14"`），而判档写的是 `day.day` ⇒ 每一轮都炸在同一行，
    台账连红 122 次、自接进常驻进程起没背成过一次。而上面那几条测试全绿 ——
    它们从头到尾只喂 `dt.date(...)`，**桩与生产的类型不一致就是假绿**（同 10-10 那回）。
    """
    from duanxian import util

    real = util.china_today()
    assert isinstance(real, str), f"前提变了：china_today() 现在给的不是串（{type(real).__name__}）"
    assert backup.kind_for(real) in ('daily', 'weekly', 'monthly')
    # 三种写法给出同一个档
    assert backup.kind_for('2026-10-17') == backup.kind_for(dt.date(2026, 10, 17)) == 'weekly'
    assert backup.kind_for('2026-11-01') == 'monthly'
    calls = []
    res = {'artifacts': [{'path': 'D:/bak/daily/d.png', 'mb': 12.3}], 'verify': {'ok': True}, 'rotated': {}}
    out = backup.run_once(day='2026-10-12', runner=lambda k: res,
                          journal=lambda name, ok=True, detail=None: calls.append((name, ok, detail)))
    assert out['ok'] is True and out['kind'] == 'daily', "循环那条路（串）与手跑那条路（date）不许分叉"
    # 认不出来的形状要照实拒，不许悄悄当成"今天"
    for bad in ('14/10/2026', '不是日期', ''):
        try:
            backup.kind_for(bad)
        except ValueError:
            continue
        raise AssertionError(f'非法日期串被放过了：{bad!r}')


def test_背成一次要记账且键名固定是backup():
    calls = []
    res = {'artifacts': [{'path': 'D:/bak/daily/daily_x.dump', 'mb': 49.6}],
           'verify': {'ok': True, 'error': None}, 'rotated': {'daily': ['old1.dump']}}
    out = backup.run_once(day=dt.date(2026, 10, 12), runner=lambda k: res,
                          journal=lambda name, ok=True, detail=None: calls.append((name, ok, detail)))
    assert out['ok'] is True and out['kind'] == 'daily'
    assert calls and calls[0][0] == 'backup' and calls[0][1] is True
    detail = calls[0][2]
    assert detail['kind'] == 'daily' and detail['artifact'].endswith('.dump')
    assert detail['mb'] == 49.6 and detail['verify_ok'] is True and detail['rotated'] == {'daily': 1}


def test_周六那一档不做恢复验证不许算失败():
    calls = []
    res = {'artifacts': [{'path': 'D:/bak/weekly/weekly_x.dump', 'mb': 275.5}],
           'verify': {'ok': None, 'error': '本次未做恢复验证'}}
    out = backup.run_once(day=dt.date(2026, 10, 17), runner=lambda k: res,
                          journal=lambda name, ok=True, detail=None: calls.append((name, ok, detail)))
    assert out['kind'] == 'weekly' and out['ok'] is True, '没验 ≠ 验不过'
    assert calls[0][1] is True and calls[0][2]['verify_ok'] is None


def test_恢复验证不通过必须算失败并说清原因():
    calls = []
    res = {'artifacts': [{'path': 'D:/bak/daily/daily_y.dump', 'mb': 49.6}],
           'verify': {'ok': False, 'error': 'pg_restore -l 列不出 TOC'}}
    out = backup.run_once(day=dt.date(2026, 10, 12), runner=lambda k: res,
                          journal=lambda name, ok=True, detail=None: calls.append((name, ok, detail)))
    assert out['ok'] is False and '恢复验证未通过' in out['reason'] and 'TOC' in out['reason']
    assert calls[0][1] is False


def test_备份抛异常也要留下一格账带异常名():
    """原先最容易漏的一种：dump 途中炸了，线程里只 print 一句，台账上"最近成功"还是上个月的。"""
    calls = []

    def boom(kind):
        raise OSError('pg_dump.exe 找不到（D:/PostgreSQL/17/bin 不在预期位置）')

    out = backup.run_once(day=dt.date(2026, 10, 12), runner=boom,
                          journal=lambda name, ok=True, detail=None: calls.append((name, ok, detail)))
    assert out['ok'] is False
    assert 'OSError' in out['reason'] and 'pg_dump' in out['reason']
    assert calls[0][0] == 'backup' and calls[0][1] is False, '失败必须进同一格台账'


def test_零字节产物不算背到且不顺手轮转(tmp_path, monkeypatch):
    """`returncode==0` 但文件是空的这种先例真有（monthly 目录里那份 0 字节的 globals，
    还有 `_失败_weekly_20260926/` 里四份 0 字节 weekly）。只看退出码会把空档记成"背到了"，
    等到真要恢复那天才发现手里什么都没有。"""
    empty = tmp_path / 'daily_20261012_000000.dump'
    empty.write_bytes(b'')
    rotated: list[str] = []
    monkeypatch.setattr(backup, 'dump', lambda kind, now=None: empty)
    monkeypatch.setattr(backup, 'rotate', lambda kind: rotated.append(kind) or [])
    monkeypatch.setattr(backup, '_record', lambda *a, **k: None)
    out = backup.run('daily')
    assert out['verify']['ok'] is False and '0 字节' in out['verify']['error']
    assert rotated == [], '这一轮没背成，不该顺手把旧的、还能用的那份删掉'


def test_测试进程里run_once硬挡真备份():
    """常驻循环挂在 uvicorn 里，而 pytest 的 TestClient 会跑 lifespan ⇒ 线程真的会起。
    只要那一轮落进 `run_at` 的补窗，就是一次 500 MB 的真 dump。与 `_spawn_stage`、
    `shells.reap` 同形：动作点自己认得测试进程，不依赖调用方记得打洞。"""
    import os as _os

    calls = []
    out = backup.run_once(day=dt.date(2026, 10, 13),
                          journal=lambda name, ok=True, detail=None: calls.append(name))
    assert out['ok'] is False and '测试进程' in out['reason'], out
    assert calls == [], "守卫那一轮不该把宿主自己的失败写进台账（测试里那一格是脏的）"


def test_宿主与CLI都走同一个执行体不各判一份():
    """接线检查：循环里必须调 `run_once`（而不是自己重判档、自己再记一份账），
    且 lifespan 真的把 `_start_backup()` 接上了 —— 写着函数没接调用点等于没这条循环。"""
    src = (ROOT / 'server.py').read_text(encoding='utf-8')
    body = src.split('def _backup_scheduler(')[1].split('\ndef ')[0]
    assert 'backup.run_once(' in body, '循环不许绕过 run_once 自己拼一份判据'
    # 台账只住 `backup.run_once` / `backup.journal_skipped` 里：循环体里再写一笔就是第二套口径
    # （docstring 里提到 `jobs.record` 不算，所以按"行首是调用"来判）。
    assert not re.search(r'^\s*jobs\.record\(', body, re.M), '循环体里不该有第二处记账调用'
    assert 'backup.journal_skipped(' in body, '让路那一轮也要留下一笔（读得出循环活着）'
    assert 'backup.journal_failure(' in body, '循环自己的异常必须记账：线程默默死掉就是"失败了没人知道"'
    start = src.split('def _start_backup(')[1].split('\n\n\n')[0]
    assert 'target=_backup_scheduler' in start and 'name="pg-backup"' in start, \
        '线程名必须在 LOOP_JOBS 清单里（名字对不上，doctor 的"缺几条循环"就永远判不准）'
    life = src.split('async def lifespan')[-1]
    assert '_start_backup()' in life.split('yield')[0], '启动了才算接上'
    cli = (ROOT / 'scripts' / 'jobs.py').read_text(encoding='utf-8')
    assert 'backup.run_once()' in cli, 'CLI 的 run-now 必须走同一个执行体'
    assert '"backup"' in cli.split('def _known()')[1].split('return')[1], '不在册 = 命令行改不动这个开关'


# ———— 2026-10-14 夜里补的一条：恢复验证的临时库名不能是固定的那一个 ————
# 现象（当天实测）：`verify.ok=false`，原因原文是「建临时库失败：ERROR: database
# "financetool_verify" already exists」。库里现查 `financetool_verify` 建于 10-13 17:44:40、
# 5,345MB、**挂着 259 条会话**：上一轮留下的临时库有人连着，`DROP ... IF EXISTS` 就会失败，
# 而旧代码把这一步的返回丢掉不看 ⇒ 下一句 `CREATE` 撞同名，屏幕上只留一句"恢复验证未通过"。
# 那一档 dump 本身是好的（528.87MB 已落盘），却被读成"备份坏了"。

def test_恢复验证的临时库名每次不同且删不掉旧库要说话(monkeypatch, tmp_path):
    seen: list[list[str]] = []

    def sql_of(argv):
        # `-tAc <SQL>` 后面还跟着别的参数（计数那一发就是），所以按位置取，别用 argv[-2]
        return argv[argv.index('-tAc') + 1] if '-tAc' in argv else ''

    def fake_run(argv, password, *, timeout=7200):
        seen.append(list(argv))
        sql = sql_of(argv).strip().lower()
        toks = sql.split()
        if toks[:3] == ['select', '1', 'from']:            # 孤儿库在不在
            return True, '1' if toks[-1].strip("'") == backup.VERIFY_DB else ''
        if toks[:2] == ['create', 'database']:
            return True, ''                                  # 名字在第 3 个词
        if toks[:2] == ['drop', 'database']:                 # 只有那个历史固定名删不掉
            return (toks[-1] != backup.VERIFY_DB), '错误：其它会话仍连接到数据库'
        if 'count(*)' in sql:
            return True, '123'
        return True, ''

    monkeypatch.setattr(backup, '_run', fake_run)
    monkeypatch.setattr(backup, '_bin', lambda tool: tool)
    monkeypatch.setattr(backup.config, 'PG_SUPERPASS', 'pw', raising=False)
    monkeypatch.setattr(backup.config, 'PG_SUPERUSER', 'postgres', raising=False)

    out = backup.verify(tmp_path / 'daily_x.dump')

    creates = [sql_of(a) for a in seen if sql_of(a).lower().startswith('create database')]
    assert len(creates) == 1, creates
    db = creates[0].split()[2]
    assert db.startswith(backup.VERIFY_DB + '_') and db != backup.VERIFY_DB, f'临时库名必须带本次运行的印记，收到 {db}'
    restores = [a for a in seen if a and a[0] == 'pg_restore']
    assert restores and restores[0][restores[0].index('-d') + 1] == db, '恢复要建在同一个名字上'
    assert out['ok'] is True and out.get('verify_db') == db, out
    # 上一轮那个删不掉的固定名库必须在结果里看得见（本档结论照算，但不许静默）
    assert out.get('stale_verify_db'), out


def test_临时库名里不许出现其它字符():
    for bad in ('financetool_verify"; drop', 'a b', ''):
        try:
            backup._db_name_or_raise(bad)   # type: ignore[arg-type]
        except ValueError:
            continue
        raise AssertionError(f'{bad!r} 不该被拼进 SQL')


# ============================================================ 10-18 跨盘副本与体检的日历判据


def _dump(tmp_path, kind, *, days_ago=1, size=2048, on=None):
    """造一份假档。`on` 把 mtime 锚在**绝对日期**上 —— 配 `today=` 的那两条用例必须要它。

    为什么：`days_ago` 是从"现在"往回数，而用例把 `today` 写死了。日历一过午夜，
    「2 天前」那份档就从"上周五"变成"本周一"，而 `backup_stale_kinds` 对 daily 用的是
    **日历判据**（dump 那天 ≥ 最近一个该背它的那天 ⇒ 不算漏）——于是同一条判据在同一条
    测试上自己翻转。2026-10-21 全量里 `test_工作日漏背一次第二天必须报出来` 变红就是这么来的
    （生产逻辑没动；前一天的日志还是绿的）。文件名里那个 20261016 才是这条用例的本意：
    "最新一份还是上周五"。顺带避开另一个坑：`days_ago=0` 的 mtime＝此刻，会被
    "正在写的那份不算过期"那一支接走，用例测的就不再是它名字说的那件事。
    """
    import os
    import time
    d = tmp_path / kind
    d.mkdir(parents=True, exist_ok=True)
    p = d / ('%s_20261016_184030.dump' % kind)
    p.write_bytes(b'0' * size)
    t = (dt.datetime.combine(on, dt.time(18, 40, 30)).timestamp() if on is not None
         else time.time() - days_ago * 86400)
    os.utime(p, (t, t))
    return p


def test_跨盘副本要核对字节数并自建目录(tmp_path):
    src = _dump(tmp_path, 'daily', size=4096)
    out = backup.mirror(src, dest_root=tmp_path / 'mirror')
    assert out['ok'] is True, out
    dst = tmp_path / 'mirror' / 'daily' / src.name
    assert dst.is_file() and dst.stat().st_size == 4096, out
    assert out['mb'] == round(4096 / 1024 / 1024, 2)


def test_跨盘副本写一半就报错不许冒充做成(tmp_path, monkeypatch):
    src = _dump(tmp_path, 'daily', size=4096)
    # 目标盘上留了一份**半截**文件（磁盘满/中途断都是这个形状）
    dst_dir = tmp_path / 'mirror' / 'daily'
    dst_dir.mkdir(parents=True)
    (dst_dir / src.name).write_bytes(b'0' * 10)
    monkeypatch.setattr(backup.shutil, 'copy2', lambda a, b: b)   # 复制"成功"但没改内容
    out = backup.mirror(src, dest_root=tmp_path / 'mirror')
    assert out['ok'] is False and '字节数对不上' in out['error'], out


def test_跨盘副本没盘就明说是没配不算做成也不算失败(tmp_path, monkeypatch):
    src = _dump(tmp_path, 'daily')
    monkeypatch.setattr(backup.config, 'BACKUP_MIRROR_DIR', Path(''))
    out = backup.mirror(src)
    assert out['ok'] is None and '没配' in out['skipped'], out


def test_目录型产物不跨盘复制并写明原因(tmp_path):
    d = tmp_path / 'monthly' / 'basebackup_x'
    d.mkdir(parents=True)
    (d / 'PG_VERSION').write_text('17\n')
    out = backup.mirror(d, dest_root=tmp_path / 'mirror')
    assert out['ok'] is None and 'basebackup' in out['skipped'], out


def test_测试进程里绝不往真盘上复制(tmp_path, monkeypatch):
    """dest_root=None 走的是配置里那棵真树（C 盘那棵 finance_backup）—— pytest 不许碰。"""
    src = _dump(tmp_path, 'daily')
    monkeypatch.setattr(backup.config, 'BACKUP_MIRROR_DIR', tmp_path.parent / 'never-written-mirror')
    out = backup.mirror(src)
    assert out['ok'] is None and '测试进程' in out['skipped'], out
    assert not (tmp_path.parent / 'never-written-mirror').exists(), '跨盘洞漏了：真往盘外长了一棵树'


def test_run_once跨盘没落地要记失败并点名是哪一半():
    res = {'kind': 'daily', 'artifacts': [{'path': 'D:/finance/TataPG_backup/daily/x.dump', 'mb': 700.0}],
           'verify': {'ok': True}, 'rotated': {},
           'mirror': {'ok': False, 'error': '[Errno 13] 拒绝访问',
                      'to': 'C:/finance_backup/TataPG/daily/x.dump'}}
    seen = {}
    backup.run_once(day=dt.date(2026, 10, 16), runner=lambda k: res,
                    journal=lambda n, **kw: seen.update({'name': n, **kw}))
    assert seen['ok'] is False, seen
    assert '跨盘副本没落地' in seen['detail']['reason'], seen
    assert '只有一份' in seen['detail']['reason'], '措辞要说清风险：这一轮只有一份档'


def test_run_once没配第二块盘不该把背成的一轮判红():
    res = {'kind': 'daily', 'artifacts': [{'path': 'D:/finance/TataPG_backup/daily/x.dump', 'mb': 700.0}],
           'verify': {'ok': True}, 'rotated': {},
           'mirror': {'ok': None, 'skipped': '没配第二块盘'}}
    seen = {}
    backup.run_once(day=dt.date(2026, 10, 16), runner=lambda k: res,
                    journal=lambda n, **kw: seen.update({'name': n, **kw}))
    assert seen['ok'] is True, seen
    assert seen['detail']['mirror_ok'] is None


def test_doctor那份日历与kind_for不许分叉():
    """体检要在依赖没装的时候也能跑，所以它自己算了一遍"这天该背哪档"。
    两处一分数，就会出现"循环背对了而体检天天红"（10-18 那条假红正是这个形状）。"""
    from scripts import manage
    d = dt.date(2026, 9, 1)
    while d <= dt.date(2026, 12, 31):
        assert manage.backup_kind_due(d) == backup.kind_for(d), d
        d += dt.timedelta(days=1)


def test_周末那晚的体检不许把daily判成过期(tmp_path):
    """10-18 实测：`daily＝52 小时前` 被报成超阈值，而周六周日按设计背的是全库 weekly。"""
    from scripts import manage
    ages = {'daily': (52.0, True, _dump(tmp_path, 'daily', on=dt.date(2026, 10, 16)), ''),
            'weekly': (4.0, True, _dump(tmp_path, 'weekly', on=dt.date(2026, 10, 17)), '')}
    stale, _busy = manage.backup_stale_kinds(ages, today=dt.date(2026, 10, 18))
    assert stale == [], stale


def test_工作日漏背一次第二天必须报出来(tmp_path):
    from scripts import manage
    # 周一（10-19）该有 daily 而没跑；周二（10-20）来查，最新一份还是上周五
    ages = {'daily': (52.0, True, _dump(tmp_path, 'daily', on=dt.date(2026, 10, 16)), ''),
            'weekly': (100.0, True, _dump(tmp_path, 'weekly', on=dt.date(2026, 10, 16)), '')}
    stale, _busy = manage.backup_stale_kinds(ages, today=dt.date(2026, 10, 20))
    assert stale == ['daily'], stale


def test_读不开的档永远算过期哪怕它很新(tmp_path):
    from scripts import manage
    ages = {'daily': (24.0, False, _dump(tmp_path, 'daily', days_ago=1), 'pg_restore 列不出 TOC'),
            'weekly': (24.0, True, _dump(tmp_path, 'weekly', days_ago=1), '')}
    stale, _busy = manage.backup_stale_kinds(ages, today=dt.date(2026, 10, 16))
    assert stale == ['daily'], stale


def test_doctor与config的跨盘默认目录不许分叉(monkeypatch):
    """两边各自算一遍同一个默认值（doctor 不 import 带 psycopg 的 `app.config`），
    分叉的那天就是"改了 .env 而体检还在看旧目录"那种假绿的第二天。"""
    from app import config
    from scripts import manage
    monkeypatch.delenv("FT_BACKUP_MIRROR_DIR", raising=False)
    # config 的模块级值在 import 时就按当时的 env 定了，这里比的是**默认串**本身
    assert str(config.BACKUP_MIRROR_DIR) == manage.BACKUP_MIRROR_DEFAULT or \
        __import__("os").environ.get("FT_BACKUP_MIRROR_DIR") is not None
    monkeypatch.setenv("FT_BACKUP_MIRROR_DIR", "D:/somewhere/else")
    assert manage.backup_mirror_dir() == Path("D:/somewhere/else")


def test_跨盘缺哪一档就要在体检里点名(tmp_path, monkeypatch):
    from scripts import manage
    mirror = tmp_path / "mirror"
    monkeypatch.setenv("FT_BACKUP_MIRROR_DIR", str(mirror))
    d = _dump(tmp_path, 'daily', size=3000)
    w = _dump(tmp_path, 'weekly', size=4000)
    ages = {'daily': (2.0, True, d, ''), 'weekly': (2.0, True, w, '')}

    gap = manage.backup_mirror_gap(ages)
    assert sorted(gap['missing']) == ['daily', 'weekly'], gap
    assert '缺' in gap['text'] and '不判红' in gap['text'], gap['text']

    (mirror / 'daily').mkdir(parents=True)
    (mirror / 'daily' / d.name).write_bytes(b'0' * 3000)
    (mirror / 'weekly').mkdir(parents=True)
    (mirror / 'weekly' / w.name).write_bytes(b'0' * 17)     # 半截：同名不同大小
    gap = manage.backup_mirror_gap(ages)
    assert gap['missing'] == ['weekly'], gap
    assert 'daily＝有' in gap['text'] and 'weekly＝缺' in gap['text'], gap['text']


def test_没配第二块盘不判红只说明(tmp_path, monkeypatch):
    from scripts import manage
    monkeypatch.setenv("FT_BACKUP_MIRROR_DIR", "")
    d = _dump(tmp_path, 'daily')
    gap = manage.backup_mirror_gap({'daily': (1.0, True, d, ''), 'weekly': (1.0, True, d, '')})
    assert gap['missing'] == [] and '没配' in gap['text'], gap


def test_跨盘缺一份只陈述不改本项成败(tmp_path, monkeypatch):
    """10-19 实测过的形状：`weekly` 那份是 10-18 18:54 背的（比这一档代码还早），
    要是"跨盘缺"也算红，这一项会连着七天每天都红一次，而那天并没有任何事失败 ——
    当场没拷成由备份那一轮自己记失败（`run()` 里 `mirror_ok is False` 整轮判失败，走「采集作业」那项）。"""
    from scripts import manage
    root = tmp_path / "backups"
    d = _dump(root, 'daily', size=128, days_ago=1)
    w = _dump(root, 'weekly', size=128, days_ago=1)
    monkeypatch.setattr(manage, "backup_dir", lambda: root)
    monkeypatch.setattr(manage, "_restorable", lambda p: (True, ""))
    monkeypatch.setenv("FT_BACKUP_MIRROR_DIR", str(tmp_path / "mirror"))
    ok, msg = manage.backup_freshness()
    assert ok is True, msg                       # 两份档都不旧、都读得开 → 本项不红
    assert "跨盘副本" in msg and "缺" in msg, msg
