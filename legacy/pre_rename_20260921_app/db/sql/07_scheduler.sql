-- ============================================================ 07 调度器
-- 实现 docs/09-调度器功能说明.md §5。
--
-- 全部增量：只建表、只加列、只加索引，不改任何现有列和约束。
-- 反复执行安全。
--
-- ★ 唯一一处会失败的地方在最后：ux_task_succeeded。它要求"同一作业同一交易日
--   至多一条 succeeded"。如果历史数据里有重复，CREATE UNIQUE INDEX 会直接报错、
--   迁移中止 —— 这是故意的，静默跳过索引就等于 §6.5 的"双保险"根本没生效，
--   而那正是这份文档反复警告的"看着配好了其实没生效"。
--   先查：
--     SELECT job_key, trade_date, count(*) FROM ops.task
--      WHERE state='succeeded' AND job_key IS NOT NULL
--     GROUP BY 1,2 HAVING count(*)>1;
--   确认这些是同一天重复执行的日志噪声（ops.task 是执行记录，不是业务数据）
--   之后，保留每组 finished_at 最新的那条：
--     DELETE FROM ops.task a USING ops.task b
--      WHERE a.state='succeeded' AND b.state='succeeded'
--        AND a.job_key=b.job_key AND a.trade_date=b.trade_date
--        AND a.task_id<b.task_id;
--   再重跑本脚本。


-- ---------------------------------------------------------------- 调度器的记忆
--
-- 为什么不能就地查 ops.task 得出这些：那张表是执行历史，一次重跑多一行 attempt，
-- 问不出"上次成功覆盖到哪个交易日""下次该几点触发"。而且"为什么没跑"这件事
-- 只有调度器自己知道 —— 没入队的任务在 ops.task 里根本不留痕。
CREATE TABLE IF NOT EXISTS ops.schedule_state (
    job_key           text PRIMARY KEY REFERENCES config.job(job_key) ON DELETE CASCADE,
    last_fire_at      timestamptz,             -- 上次真正入队的时刻（不是执行完成时刻）
    last_fire_slot    text,                    -- 上次消费的触发槽，防同槽重复触发
    next_fire_at      timestamptz,             -- 算出来的下次触发。页面显示这一列，
                                               -- 不显示 cron_expr 原文（两者不一致
                                               -- 本身就是配置出错的信号）
    last_success_date date,                    -- 最近一次成功覆盖到的交易日
    last_result       text,                    -- fired|skipped|failed|error
    consecutive_fail  int    NOT NULL DEFAULT 0,  -- 熔断依据，见 §7.5
    missed_count      int    NOT NULL DEFAULT 0,  -- 因调度器停机跳过的槽数
    note              text,                    -- "为什么没跑"，页面直接显示
    cron_used         text,                    -- 算 next_fire_at 时用的那个 cron_expr。
                                               -- 和 config.job.cron_expr 不一致说明表达式
                                               -- 被改过，next_fire_at 作废重算 —— 不记这一笔，
                                               -- 把 `0 8 * * *` 改成 `30 15 * * *` 之后，
                                               -- 旧算出来的明早 8 点仍会被当成"下次触发"
    updated_at        timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE ops.schedule_state IS
 '调度器记忆：每个作业一行。last_fire_slot 防同槽重触发，next_fire_at 是算出来的下次触发时刻，'
 'note 回答"这个作业为什么今天没跑"。';


-- ---------------------------------------------------------------- ops.task 增列
ALTER TABLE ops.task ADD COLUMN IF NOT EXISTS enqueued_by    text NOT NULL DEFAULT 'web';
ALTER TABLE ops.task ADD COLUMN IF NOT EXISTS fire_slot      text;
ALTER TABLE ops.task ADD COLUMN IF NOT EXISTS next_retry_at  timestamptz;
ALTER TABLE ops.task ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz;
ALTER TABLE ops.task ADD COLUMN IF NOT EXISTS claimed_by     text;

COMMENT ON COLUMN ops.task.enqueued_by IS
 '谁排的队伍：scheduler=自动到点 / web=页面上点的 / cli=命令行。'
 '排查"这个作业怎么自己跑了"只看这一列。';
COMMENT ON COLUMN ops.task.fire_slot IS
 '触发槽标识（如 2026-09-20T15:10）。cron `10 15,16,17` 一天三个槽，'
 '配合 schedule_state.last_fire_slot 保证同一槽只消费一次。';
COMMENT ON COLUMN ops.task.lease_expires_at IS
 'worker 心跳租约。worker 每 30 秒续 2 分钟；过期即视为 worker 已死，'
 '任务由 requeue_stale_running() 重新入队。不用 owner_pid 判：worker 是常驻进程，'
 '重启后 pid 变了会把还活着的子进程误判为死，见 §7.3。';


-- 同一作业同一交易日只允许一条成功记录 —— §6.5 "on_ready 主触发 + cron 兜底"
-- 成立的前提。注意这和已有的 ux_task_job_cap_date_attempt（含 attempt）并存：
-- 那条允许无限重试，这条保证成功只有一次。
CREATE UNIQUE INDEX IF NOT EXISTS ux_task_succeeded
    ON ops.task (job_key, trade_date)
    WHERE state = 'succeeded' AND job_key IS NOT NULL;

-- worker 拉任务的过滤条件走这个部分索引，避免全表扫 pending
CREATE INDEX IF NOT EXISTS ix_task_pending
    ON ops.task (created_at) WHERE state = 'pending';


-- ---------------------------------------------------------------- 就绪判定落表
-- docs/04 设计的 on_ready 触发要读这张表。表结构早就有（02_schema.sql:917），
-- 但 mark_ready() 零调用、表是空的。这里只补一个按时间倒序的索引，
-- 让调度器每 tick 的"这个数据集到哪天了"查询不吃全表。
CREATE INDEX IF NOT EXISTS ix_ready_date
    ON ops.data_ready (dataset_key, trade_date DESC);


-- ---------------------------------------------------------------- 决策日志
-- 只记 fire 与 skip，**不记 wait**：wait 每个 tick 都会产生一条（15 秒一条），
-- 一天几万行，把有用的部分淹掉。skip 是"这个槽消费了但没跑"，事后必须能查；
-- fire 是"入队了"。dry-run 期间的判定也记在这里（action='dry'），
-- 这样"放开之前先对着实库看几天决策"有东西可看。
CREATE TABLE IF NOT EXISTS ops.schedule_log (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    job_key    text        NOT NULL,
    slot       text,
    action     text        NOT NULL,      -- fire|dry|skip|error|fuse
    task_id    bigint,                    -- fire 时指向入队出来的那条 ops.task
    reason     text        NOT NULL,
    dry_run    boolean     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS ix_schedule_log_at  ON ops.schedule_log (at DESC);
CREATE INDEX IF NOT EXISTS ix_schedule_log_job ON ops.schedule_log (job_key, at DESC);
COMMENT ON TABLE ops.schedule_log IS
 '调度决策流水。回答"这个作业昨天为什么没跑"只看这张表。';


-- ---------------------------------------------------------------- 进程心跳
-- **为什么不塞进 config.setting**：那张表上挂着 ops.trg_config_history 行级触发器
-- （04_constraints.sql 里自动给每张 config.* 表 attach），每 15 秒写一次心跳
-- 会一天灌进 5,760 条历史行，把真正需要的配置变更回滚记录淹掉。
CREATE TABLE IF NOT EXISTS ops.scheduler_heartbeat (
    role         text PRIMARY KEY,        -- 'scheduler' | 'worker'
    pid          bigint,
    hostname     text,
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    meta         jsonb
);
COMMENT ON TABLE ops.scheduler_heartbeat IS
 '常驻进程存活证明。页面据此判"调度器是不是还在跑"——'
 '没有这一行，自动化最大的风险从"跑错"变成"以为在跑其实没跑"（§7.1）。';


-- ---------------------------------------------------------------- 调度器配置项
-- 全部默认"关"。scheduler_enabled=false 时调度器进程照常跑、照常发心跳，
-- 但只打日志不入队 —— 自动化出问题时回到今天的手动模式不需要重启任何进程。
-- 键写在 07 里而不是 05_seed.sql：这是本迁移自带的配套，不改动已有种子文件
-- （改了会让 ops.schema_migration 里 05 的 checksum 变动，和实际已应用的库不符）。
INSERT INTO config.setting (key, value, label, category) VALUES
('scheduler_enabled', 'false',
 '调度器总开关。关掉后调度进程只发心跳、不再入队，数据更新回到手动模式', 'scheduler'),
('scheduler_dry_run', 'true',
 '试运行。判定链照常走、原因照常写，但不真的入队。对着实库核对几天决策再放开', 'scheduler'),
('scheduler_web_via_queue', 'false',
 '页面上的「运行」按钮改为只入队、由 worker 执行。关掉＝沿用今天的行为（web 进程直接起子进程）。'
 '打开后 concurrency_key 才真正生效，代价是点下去最多等一个 tick（15 秒）才开始跑', 'scheduler'),
('scheduler_tick_sec', '15',
 '调度器轮询间隔（秒）。改完下个 tick 生效，不用重启', 'scheduler'),
('scheduler_max_parallel', '1',
 'worker 同时执行几个作业。先保持 1：vipdoc 是本机单份文件，两个采集并行只会互相拖慢', 'scheduler'),
('scheduler_catchup_days', '5',
 '调度器停机后最多往前补几个交易日的错过槽。下界还受 md.stock_daily 最新日约束', 'scheduler'),
('scheduler_stop_retry_after', '3',
 '连续失败几次后自动把该作业 enabled 置 false 并等人工处理（§7.5）。0＝不熔断', 'scheduler'),
('quality_gate_override_date', 'null',
 '质量门禁豁免的交易日（"YYYY-MM-DD"）。用于确认是规则本身写错了、需要强行放行的场合。'
 'null＝不豁免', 'scheduler')
ON CONFLICT (key) DO NOTHING;


-- ---------------------------------------------------------------- 菜单
-- 新页面「调度器」挂在**运维**分组（seq 135，落在数据获取 125 与 SQL 查询台 130
-- 之间）。写在 07 而不是 05_seed.sql：菜单是数据，但这一项是随本迁移一起交付的，
-- 放进 05 会让已应用库里的 05 checksum 对不上。
--
-- 不并进 /jobs 而是单开一页：/jobs 回答"跑过什么"（ops.task 执行历史），
-- 这一页回答"什么时候该跑、为什么没跑"（schedule_state + schedule_log）。
INSERT INTO config.menu (menu_key, label, route, icon, seq, group_name, need_role) VALUES
('schedule', '调度器', '/schedule', 'clock', 135, '运维', NULL)
ON CONFLICT (menu_key) DO UPDATE SET
    label = EXCLUDED.label, route = EXCLUDED.route, icon = EXCLUDED.icon,
    seq = EXCLUDED.seq, group_name = EXCLUDED.group_name;


-- ---------------------------------------------------------------- 授权
-- 沿用 04_constraints.sql 末尾的角色口径。etl 需要写 data_ready（采集脚本用），
-- app 需要读写 schedule_state 与整条任务队列。
GRANT SELECT, UPDATE, INSERT, DELETE ON ops.schedule_state       TO ft_app, ft_admin;
GRANT SELECT, INSERT, DELETE         ON ops.schedule_log         TO ft_app, ft_admin;
GRANT SELECT, UPDATE, INSERT         ON ops.scheduler_heartbeat  TO ft_app, ft_admin;
GRANT SELECT, UPDATE, INSERT, DELETE ON ops.task                 TO ft_app, ft_admin, ft_etl;
GRANT SELECT ON ops.schedule_state, ops.schedule_log, ops.scheduler_heartbeat
                                                               TO ft_reader;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA ops TO ft_app;
