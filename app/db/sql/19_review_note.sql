-- -*- coding: utf-8 -*-
-- 19_review_note.sql
-- 两张**用户手写**的表（需求：用户 2026-09-28）：
--   ys.review_note  每日复盘笔记（原来在桌面 Excel「做与直觉相反的事」的 2026 sheet 里手写，132 行真实记录）
--   ys.reminder     关注提醒（"今天关注了这个票、等回调，7 日后提醒我"）+ 到期时间轴
--
-- 为什么放在 `ys`：这个 schema 已经装着用户自己的东西（`ys.watchlist_member_snapshot`、
-- `ys.watchlist_backup`、`ys.conversation`），而 `config` 装的是注册表与系统设置。
-- 笔记与提醒都是"这个人写的"，不是"系统配的"。owner 是 ft_admin，所以建表用 admin 角色跑。
--
-- ⚠️ 13 个正文列一律 `text`，**不许改成 varchar(n)**：实测那份 Excel 里最长的
-- 一格「市场看法·对大盘板块」写了 6186 字，「对个股定计划」1231 字。定长会静默截断，
-- 而截掉的那半段恰恰是当时为什么这么想的证据。
--
-- ⚠️ `machine_baseline` 存的是**第一次保存时那一格机器给的原文**，一旦写进就不再覆盖。
-- 界面要靠"当前值 == baseline"判断「初始为机器填入·未改」还是「初始为机器填入·你已改」。
-- 如果每次保存都重写 baseline，用户改完再存一次，"已改"就翻回"未改" —— 那份草稿到底
-- 是不是自己写的，就永远问不出来了。合并逻辑在 `duanxian/review_notes.py:save_note()`。
--
-- ⚠️ 提醒的到期日按**交易日**算（用户 2026-09-28 选定口径），而本机交易日历
-- `reference_data.trade_calendar` 只覆盖到 2026-12-31。超出去的部分**不冒充交易日**：
-- `calendar_covered=false` 时 `due_date` 只是"跳过周末"的估计，界面必须这么写。
-- 这条不是保险起见：静默用工作日推算会把"第 7 个交易日"变成"下周三"，而长假中间差三天。

create table if not exists ys.review_note (
  trade_date          date        not null,
  -- ① 大环境（当天/隔夜的外部背景：战争、政策、外围市场…）
  env                 text        not null default '',
  -- ② 自己判断（日期**前一天**情况）：这一栏是"开盘前写的预判"，不是收盘后的复述
  plan_market         text        not null default '',   -- 对大盘、板块
  plan_stock          text        not null default '',   -- 对个股定计划
  -- ③ 反思复盘（独立一列，Excel 里 E 列）
  reflect             text        not null default '',
  -- ④ 市场看法和实际走势（日期**当天**情况）
  view_market         text        not null default '',   -- 对大盘、板块
  view_news           text        not null default '',   -- 政策、消息
  view_stock          text        not null default '',   -- 对个股
  view_bigv           text        not null default '',   -- 关注的主要大V观点
  -- ⑤ 策略的执行复盘、反思（独立一列，Excel 里 J 列）
  exec_review         text        not null default '',
  -- ⑥ 复盘三件
  keep_doing          text        not null default '',   -- 继续做 Continue doing
  stop_doing          text        not null default '',   -- 停止做 Stop doing
  start_doing         text        not null default '',   -- 开始做 Start to do
  -- ⑦ 规律总结
  pattern             text        not null default '',
  -- 来源标记：{列名: 第一次保存时该列的机器原文}。只增不覆盖，见文件头那条 ⚠️
  machine_baseline    jsonb       not null default '{}'::jsonb,
  machine_generated_at timestamptz,                      -- 这批默认值是哪一刻算出来的
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  constraint review_note_pk primary key (trade_date)
);

comment on table  ys.review_note is '每日复盘笔记（用户手写）。列结构照桌面 Excel「做与直觉相反的事」2026 sheet 的 13 个正文列，一格一事；每列都是 text（实测最长 6186 字），定长会截掉证据。本机默认值只作草稿预填，来源留在 machine_baseline 里';
comment on column ys.review_note.trade_date is '这一份笔记属于哪个行情日（交易日，不是自然日）。周末/节假日写不写由界面决定，库里不拦';
comment on column ys.review_note.env is '大环境：战争/政策/外围这类当天背景。原表里用户写得最短的一列（实测最长 141 字）';
comment on column ys.review_note.plan_market is '开盘**前**对大盘与板块的预判。与 view_market（当天实际走势）配成一对，隔一天再看才知道当初判对了没 —— 所以这两列不许合并';
comment on column ys.review_note.plan_stock is '对个股定的计划（买哪个、什么价位、仓位）。这是自己的计划，不是建议，也不接入任何 AI prompt（与交易日志同一条规矩）';
comment on column ys.review_note.reflect is '反思复盘（独立一列）。本机没有任何可预填的事实源，这一格只能人写';
comment on column ys.review_note.view_market is '当天大盘与板块的实际走势与看法。默认值给的是派生情绪指标与涨跌家数这类事实，不是看法';
comment on column ys.review_note.view_news is '政策、消息。默认值来自本机快讯（~/.duanxian-agents/news/news-<date>.jsonl）的标题，标题是来源原话、不是结论';
comment on column ys.review_note.view_stock is '对个股的看法。默认值给涨停池的板数/首封/题材串；题材串是来源归因，不是公司确认事实';
comment on column ys.review_note.view_bigv is '关注的主要大V观点。⚠️ 本机没有任何大V观点数据源（没有社媒接入、没有观点库），默认值恒为空 —— 这一格出现任何"机器填入"标记都该当成缺陷来查';
comment on column ys.review_note.exec_review is '策略的执行复盘：计划的与原计划差在哪。默认值取自本机交易日志里**当天录入的那几笔**（含"计划外"标记与备注），那是记录，不是评分';
comment on column ys.review_note.keep_doing is '继续做（Continue doing）';
comment on column ys.review_note.stop_doing is '停止做（Stop doing）';
comment on column ys.review_note.start_doing is '开始做（Start to do）';
comment on column ys.review_note.pattern is '规律总结。跨日才成立的东西，单日没有默认值可给';
comment on column ys.review_note.machine_baseline is '第一次保存时那一格机器给的原文 {列名: 文本}。**只增不覆盖**：界面靠"当前值==baseline"区分「未改」与「你已改」，覆盖写会让这个区分翻面';
comment on column ys.review_note.machine_generated_at is '这一批默认值算出来的时刻。默认值的判据里有数据日期，所以"什么时候算的"必须交得出——数据入库后重算会得到不一样的数';

create table if not exists ys.reminder (
  id               bigint generated always as identity primary key,
  kind             text        not null default 'stock',
  security_code    text,                                  -- kind='stock' 时的 6 位代码；其余两类可空
  security_name    text,
  note             text        not null default '',       -- 要盯的那件事（"等回调到位再看不看"）
  reason           text        not null default '',       -- 当时为什么定这个提醒 —— 到期时最值钱的一栏
  base_date        date        not null,                  -- 起算日：到期=它**之后**第 sessions 个交易日
  sessions         smallint    not null,                  -- 几个交易日（不含起算日当天）
  due_date         date        not null,                  -- 由日历算出的到期日
  calendar_covered boolean     not null default true,      -- false=超出日历覆盖，due_date 只是跳周末的估计
  status           text        not null default 'open',
  done_at          timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  constraint reminder_kind_check check (kind in ('stock','theme','free')),
  constraint reminder_status_check check (status in ('open','done','cancelled')),
  constraint reminder_sessions_check check (sessions between 1 and 250)
);

comment on table  ys.reminder is '关注提醒：一条"盯到哪天"的备忘 + 按到期日排的时间轴。到期日按交易日算（本机日历覆盖 1990-12-19 ~ 2026-12-31）';
comment on column ys.reminder.kind is 'stock=个股 / theme=板块或题材 / free=不带标的的一句话。分三类是因为"7 日后提醒"对个股有涨跌可查，对自由提醒只能回到自己写的那段话';
comment on column ys.reminder.base_date is '起算日。到期日 = base_date **之后**第 sessions 个交易日，不含当天 —— 填"今天 7 日"意思是 7 个交易日后，不是今天加 7 天';
comment on column ys.reminder.sessions is '交易日数（1~250）。界面给 3/5/10/20 四档按钮；这个数存原值，日历延长后可重算';
comment on column ys.reminder.calendar_covered is 'false＝到期日超出 reference_data.trade_calendar 覆盖（当前到 2026-12-31），此时 due_date 是"跳过周末"的估计值，界面必须这么标，不能当交易日';
comment on column ys.reminder.reason is '当时为什么定这个提醒（"等它回踩二十日线"）。到期日判断"当初的想法还成立吗"全靠这一栏';

create index if not exists reminder_due_idx on ys.reminder (status, due_date, id);
create index if not exists reminder_code_idx on ys.reminder (security_code, due_date desc);

grant select, insert, update, delete on ys.review_note to ft_app, ft_etl;
grant select on ys.review_note to ft_reader;
grant select, insert, update, delete on ys.reminder to ft_app, ft_etl;
grant select on ys.reminder to ft_reader;
-- identity 序列：只有 ft_app 会插新行（界面与接口都走 app 角色），etl 不建提醒所以不给
grant usage, select on sequence ys.reminder_id_seq to ft_app;
grant usage on schema ys to ft_app, ft_etl, ft_reader;
