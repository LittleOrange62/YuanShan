-- =============================================================================
-- FinanceTool 03 · 十三张 config 注册表（"积木城堡"的可配置面）
-- 执行身份：ft_admin，连接库 financetool
-- 幂等：可重复执行
--
-- 设计原则：凡是"以后可能要改"的东西都存在表里，页面上能点。
-- 判断标准：如果一个改动需要改 Python 文件 + 重启服务，那它设计错了。
-- =============================================================================

-- ---------------------------------------------------------------- ① 数据源
CREATE TABLE IF NOT EXISTS config.data_source (
    source_key   text PRIMARY KEY,
    name         text NOT NULL,
    kind         text NOT NULL,          -- file_binary|file_csv|http_json|jsonrpc|db
    locator      text NOT NULL,          -- 路径模板/URL，支持 {market} {code} 占位
    parser_key   text,                   -- 解析器标识（代码提供的积木）
    options      jsonb NOT NULL DEFAULT '{}',
    health_check jsonb,                  -- {"kind":"tcp","host":"127.0.0.1","port":17709}
    priority     int  NOT NULL DEFAULT 100,
    enabled      boolean NOT NULL DEFAULT true,
    note         text,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- ② 字段口径
CREATE TABLE IF NOT EXISTS config.field_spec (
    id          bigserial PRIMARY KEY,
    table_name  text NOT NULL,
    column_name text NOT NULL,
    label_cn    text NOT NULL,
    unit        text NOT NULL,           -- yuan|shares|pct|ratio|count|date|text|none
    dtype       text NOT NULL,
    value_range jsonb,                   -- 硬性值域，越界=error
    sane_range  jsonb,                   -- 合理量级，越界=warning
    formula     text,                    -- 计算口径，人类可读；文档由此生成
    source_key  text REFERENCES config.data_source(source_key) ON DELETE SET NULL,
    display     jsonb NOT NULL DEFAULT '{}',  -- {"decimals":2,"scale":100000000,"suffix":"亿"}
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (table_name, column_name)
);

-- ---------------------------------------------------------------- ③ 数据集
CREATE TABLE IF NOT EXISTS config.dataset (
    dataset_key text PRIMARY KEY,
    name        text NOT NULL,
    grain       text NOT NULL,           -- daily|minute|snapshot|static
    source_keys text[] NOT NULL DEFAULT '{}',
    ready_rule  jsonb NOT NULL DEFAULT '{}',
    retention   jsonb,
    enabled     boolean NOT NULL DEFAULT true,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- ④ 校验规则
CREATE TABLE IF NOT EXISTS config.quality_rule (
    rule_key    text PRIMARY KEY,
    name        text NOT NULL,
    scope       text NOT NULL,           -- row|table|cross_day|cross_table|score
    severity    text NOT NULL,           -- error|warning|info
    target      text NOT NULL,
    check_kind  text NOT NULL,           -- sql_bool|builtin|expr
    check_body  text NOT NULL,
    threshold   jsonb,
    blocking    boolean NOT NULL DEFAULT false,
    enabled     boolean NOT NULL DEFAULT true,
    description text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_qrule_sev  CHECK (severity IN ('error','warning','info')),
    CONSTRAINT ck_qrule_kind CHECK (check_kind IN ('sql_bool','builtin','expr'))
);

-- ---------------------------------------------------------------- ⑤ 指标（页面上写公式）
CREATE TABLE IF NOT EXISTS config.indicator (
    ind_key     text PRIMARY KEY,
    name        text NOT NULL,
    kind        text NOT NULL,           -- window|expr|rank|crosssec
    expr        text NOT NULL,
    params      jsonb NOT NULL DEFAULT '{}',
    depends_on  text[] NOT NULL DEFAULT '{}',
    output_unit text,
    decimals    int  NOT NULL DEFAULT 4,
    builtin     boolean NOT NULL DEFAULT false,   -- 内置的不可删
    enabled     boolean NOT NULL DEFAULT true,
    note        text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_ind_kind CHECK (kind IN ('window','expr','rank','crosssec'))
);

-- ---------------------------------------------------------------- ⑥ 功能点（插槽）
CREATE TABLE IF NOT EXISTS config.capability (
    cap_key     text PRIMARY KEY,
    name        text NOT NULL,
    category    text NOT NULL,           -- factor|screen|report|derive
    output_kind text NOT NULL,           -- score|series|table|file
    output_spec jsonb NOT NULL DEFAULT '{}',
    depends_on  text[] NOT NULL DEFAULT '{}',
    requires    jsonb NOT NULL DEFAULT '{}',  -- {"datasets":["md.stock_daily"],"lookback_days":250}
    enabled     boolean NOT NULL DEFAULT true,
    description text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- ⑦ 脚本（积木）
CREATE TABLE IF NOT EXISTS config.script (
    script_id     bigserial PRIMARY KEY,
    script_key    text UNIQUE NOT NULL,
    name          text NOT NULL,
    path          text NOT NULL,         -- 相对 app/compute/strategies/
    entry         text NOT NULL DEFAULT 'run',
    version       text,
    param_schema  jsonb NOT NULL DEFAULT '{}',   -- JSON Schema，前端据此生成表单
    provides      text[] NOT NULL DEFAULT '{}',
    checksum      text,
    builtin       boolean NOT NULL DEFAULT false,
    enabled       boolean NOT NULL DEFAULT true,
    registered_at timestamptz NOT NULL DEFAULT now(),
    last_ok_at    timestamptz,
    last_error    text
);

-- ---------------------------------------------------------------- ⑧ 绑定（功能点↔脚本）
CREATE TABLE IF NOT EXISTS config.binding (
    binding_id bigserial PRIMARY KEY,
    cap_key    text   NOT NULL REFERENCES config.capability(cap_key) ON DELETE CASCADE,
    script_id  bigint NOT NULL REFERENCES config.script(script_id)   ON DELETE CASCADE,
    weight     numeric(8,4) NOT NULL DEFAULT 1.0,
    seq        int    NOT NULL DEFAULT 0,
    params     jsonb  NOT NULL DEFAULT '{}',
    enabled    boolean NOT NULL DEFAULT true,
    note       text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (cap_key, script_id)
);
CREATE INDEX IF NOT EXISTS ix_binding_cap ON config.binding (cap_key, enabled);

-- ---------------------------------------------------------------- ⑨ 作业与触发
CREATE TABLE IF NOT EXISTS config.job (
    job_id          bigserial PRIMARY KEY,
    job_key         text UNIQUE NOT NULL,
    name            text NOT NULL,
    kind            text NOT NULL DEFAULT 'compute',  -- ingest|compute|quality|report|notify
    caps            text[] NOT NULL DEFAULT '{}',
    trigger_kind    text NOT NULL,        -- cron|on_ready|chain|manual
    cron_expr       text,
    ready_on        text[],
    after_job       text,
    scope           jsonb NOT NULL DEFAULT '{"universe":"all"}',
    params          jsonb NOT NULL DEFAULT '{}',
    only_trade_day  boolean NOT NULL DEFAULT true,
    max_retry       int NOT NULL DEFAULT 2,
    timeout_sec     int NOT NULL DEFAULT 1800,
    concurrency_key text,
    enabled         boolean NOT NULL DEFAULT true,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_job_trigger CHECK (trigger_kind IN ('cron','on_ready','chain','manual'))
);

-- ---------------------------------------------------------------- ⑩ 筛选条件类型
CREATE TABLE IF NOT EXISTS config.screen_rule (
    rule_key     text PRIMARY KEY,
    name         text NOT NULL,
    param_schema jsonb NOT NULL DEFAULT '{}',
    sql_template text NOT NULL,           -- 仅允许出现在 WHERE 片段位置，占位符参数化绑定
    enabled      boolean NOT NULL DEFAULT true,
    builtin      boolean NOT NULL DEFAULT false,
    note         text
);

CREATE TABLE IF NOT EXISTS config.screen_preset (
    preset_id  bigserial PRIMARY KEY,
    name       text NOT NULL,
    owner      text NOT NULL DEFAULT current_user,
    conditions jsonb NOT NULL,
    is_shared  boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner, name)
);

-- ---------------------------------------------------------------- ⑪ 图表
CREATE TABLE IF NOT EXISTS config.chart (
    chart_key  text PRIMARY KEY,
    name       text NOT NULL,
    chart_type text NOT NULL,             -- candlestick|line|bar|hist|scatter|heatmap|pie|table
    data_query text NOT NULL,             -- 命名查询 key
    encoding   jsonb NOT NULL DEFAULT '{}',
    options    jsonb NOT NULL DEFAULT '{}',
    params     jsonb NOT NULL DEFAULT '{}',
    enabled    boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- ⑫ 推送
CREATE TABLE IF NOT EXISTS config.notify_channel (
    channel_key text PRIMARY KEY,
    name        text NOT NULL,
    kind        text NOT NULL,            -- feishu|dingtalk|wecom|email|webhook|local_file
    endpoint    text NOT NULL,
    secret_ref  text,                     -- 指向凭据存储的键名，不存明文
    template    text,
    enabled     boolean NOT NULL DEFAULT true,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS config.notify_rule (
    rule_key      text PRIMARY KEY,
    name          text NOT NULL,
    trigger       text NOT NULL,          -- job_done|job_failed|quality_error|screen_hit|score_cross
    condition     jsonb,
    channel_key   text NOT NULL REFERENCES config.notify_channel(channel_key) ON DELETE CASCADE,
    payload_query text,
    enabled       boolean NOT NULL DEFAULT true,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- ⑬ 页面布局与菜单
CREATE TABLE IF NOT EXISTS config.page_layout (
    layout_id bigserial PRIMARY KEY,
    page_key  text NOT NULL,
    slot      int  NOT NULL,
    width     int  NOT NULL DEFAULT 6,    -- 12 栅格
    height    int  NOT NULL DEFAULT 320,
    card_kind text NOT NULL,              -- stat|chart|table|score_panel|task_list|markdown|iframe
    card_ref  text,
    title     text,
    options   jsonb NOT NULL DEFAULT '{}',
    visible   boolean NOT NULL DEFAULT true,
    UNIQUE (page_key, slot),
    CONSTRAINT ck_card_kind CHECK (card_kind IN
        ('stat','chart','table','score_panel','task_list','markdown','iframe'))
);

CREATE TABLE IF NOT EXISTS config.menu (
    menu_key   text PRIMARY KEY,
    label      text NOT NULL,
    route      text NOT NULL,
    icon       text,
    seq        int  NOT NULL DEFAULT 100,
    group_name text,
    need_role  text,                      -- NULL=所有登录用户；'admin'=仅管理员
    visible    boolean NOT NULL DEFAULT true
);


-- ---------------------------------------------------------------- 用户与自选
CREATE TABLE IF NOT EXISTS config.app_user (
    username     text PRIMARY KEY,
    password_hash text NOT NULL,          -- argon2，不存明文
    role         text NOT NULL DEFAULT 'user',   -- user|admin
    display_name text,
    enabled      boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

CREATE TABLE IF NOT EXISTS config.watchlist_group (
    group_id   bigserial PRIMARY KEY,
    name       text NOT NULL,
    owner      text NOT NULL DEFAULT current_user,
    is_focus   boolean NOT NULL DEFAULT false,   -- "重点关注"是自选的一个特殊分组
    seq        int NOT NULL DEFAULT 100,
    note       text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner, name)
);

CREATE TABLE IF NOT EXISTS config.watchlist_stock (
    group_id  bigint NOT NULL REFERENCES config.watchlist_group(group_id) ON DELETE CASCADE,
    ts_code   text   NOT NULL,
    added_at  timestamptz NOT NULL DEFAULT now(),
    cost      numeric(12,4),
    note      text,
    PRIMARY KEY (group_id, ts_code)
);

-- 通用键值配置（不值得单独建表的杂项）
CREATE TABLE IF NOT EXISTS config.setting (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    label      text,
    category   text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- ⑮ 通达信弹窗规则
-- 通达信在启动、登录、下载各阶段会弹一堆窗口，出现时机不固定，
-- 所以看门狗（app/tdx_client/watchdog.py）按规则处理而不是按流程写死。
-- 放在表里的目的是**遇到没见过的弹窗时加一行就行，不用改代码**。
--
-- action 四种，语义差别很大，别混：
--   ignore  放过。给"绝不能关"的窗口用 —— TQ数据设置 被关掉整条链路就断了。
--   alert   只报不关。给"关掉也解决不了"的用 —— "已在别处登录"关了行情也不恢复，
--           必须重新登录；磁盘满了关掉下载照样失败。这类要人来决定。
--   close   关掉。公告、升级提示这类。
--   button  点指定按钮（button_text）。模态框只认按钮、不认 WM_CLOSE 时用。
--
-- **priority 小的先匹配。** ignore/alert 的 priority 必须比 close 小，
-- 否则 `提示` 这种宽泛规则会先把 `TQ数据设置` 捞走然后关掉它。
CREATE TABLE IF NOT EXISTS config.popup_rule (
    rule_key    text PRIMARY KEY,
    match_type  text NOT NULL DEFAULT 'title',   -- title|class
    pattern     text NOT NULL,                   -- 包含匹配，不是正则
    action      text NOT NULL DEFAULT 'close',
    button_text text,                            -- action='button' 时点哪个按钮
    priority    int  NOT NULL DEFAULT 100,
    is_active   boolean NOT NULL DEFAULT true,
    note        text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_popup_match CHECK (match_type IN ('title','class')),
    CONSTRAINT ck_popup_action CHECK (action IN ('ignore','alert','close','button')),
    -- action='button' 却不给按钮文字，等于什么都不做还占着优先级
    CONSTRAINT ck_popup_button CHECK (action <> 'button' OR button_text IS NOT NULL),
    CONSTRAINT ck_popup_pattern CHECK (length(btrim(pattern)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_popup_rule_active
    ON config.popup_rule (priority, rule_key) WHERE is_active;


-- =============================================================================
-- 外部数据源注册表
-- =============================================================================
-- **为什么要建表而不是写死在代码里**：数据源会坏、会变、会限额，而且坏法各不相同。
-- 实测过的三种坏法必须能区分开，否则页面上一片红，人根本不知道该修哪个：
--   ok             通
--   degraded       通但不稳（东财系实测同一时段连续 3 次 RemoteDisconnected）
--   no_permission  接口存在但没买权限（Tushare 的 margin / margin_detail /
--                  top_list / top_inst / moneyflow / ths_index 全部 40203）。
--                  **这不是故障，页面上要显示为灰而不是红** —— 标成故障会让人
--                  一直去查网络，而实际上要做的是换源或买权限。
--   broken         接口自身有 bug（stock_margin_szse 在 akshare 1.18.88 里
--                  抛 Length mismatch，跟网络无关，重试一万次也没用）
--   down           连不上
--
-- quota_limit / quota_used 用于有配额的源（Tushare 按积分限频、妙想按 token 计费）。
-- 没有配额概念的源留空，页面上就不显示余量条，而不是显示 0%。
--
-- ---- layer / ds_role / cost / tagline（2026-08-30 为「数据源管理」加的）----
-- health 回答的是"现在通不通"，这四列回答的是"该先用谁"，两件事分开存：
--   layer    p0    通达信本地 vipdoc —— 有本地文件就不出网，这条是硬优先级
--            main  主力层：日常生产真正在用的
--            base  基座层：历史底座 / 回测口径的参照
--            assist 辅助层：交叉验证、随手查，缺了不影响跑批
--   ds_role  职责一句话（"日常生产主力" / "历史K线补充" / "辅助校准"）
--   cost     费用口径（"免费" / "免费额度" / "免费·Key已配"）—— 不写价格数字，
--            额度上限在 quota_limit 里，这里只写性质
--   tagline  它凭什么在这一层（"覆盖面最广" / "匿名免注册"）
-- 列名用 ds_role 而不是 role：`role` 在本项目里已经专指数据库连接角色
-- （engine.query(role='app')），同名两义查起来会串。
CREATE TABLE IF NOT EXISTS config.datasource (
    ds_key       text PRIMARY KEY,
    name         text NOT NULL,
    kind         text NOT NULL,              -- package|http|mcp|local
    provider     text,                       -- akshare|tushare|eastmoney|...
    endpoint     text,                       -- 探测用的函数名或 URL
    auth_ref     text,                       -- **只放环境变量名，绝不放密钥本身**
    layer        text NOT NULL DEFAULT 'assist',  -- p0|main|base|assist
    ds_role      text,                       -- 职责：日常生产主力 / 历史底座 / …
    cost         text,                       -- 费用性质：免费 / 免费额度 / …
    tagline      text,                       -- 一句话特点
    quota_limit  bigint,
    quota_used   bigint,
    quota_reset  text,                       -- 'daily'|'monthly'|NULL
    health       text NOT NULL DEFAULT 'unknown',
    last_ok_at   timestamptz,
    last_check   timestamptz,
    last_err     text,
    latency_ms   int,
    row_count    int,                        -- 最近一次探测拿到多少行，0 行也是信号
    probe_spec   jsonb NOT NULL DEFAULT '{}',-- 探测参数：{"fn":"...","args":{...}}
    seq          int NOT NULL DEFAULT 100,
    is_active    boolean NOT NULL DEFAULT true,
    note         text,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_ds_kind CHECK (kind IN ('package', 'http', 'mcp', 'local')),
    CONSTRAINT ck_ds_layer CHECK (layer IN ('p0', 'main', 'base', 'assist')),
    CONSTRAINT ck_ds_health CHECK (health IN (
        'unknown', 'ok', 'degraded', 'no_permission', 'broken', 'down')),
    CONSTRAINT ck_ds_quota CHECK (quota_limit IS NULL OR quota_limit > 0)
);

-- 老库补列（`CREATE TABLE IF NOT EXISTS` 对已存在的表什么都不做）。
ALTER TABLE config.datasource
    ADD COLUMN IF NOT EXISTS layer   text NOT NULL DEFAULT 'assist',
    ADD COLUMN IF NOT EXISTS ds_role text,
    ADD COLUMN IF NOT EXISTS cost    text,
    ADD COLUMN IF NOT EXISTS tagline text;
DO $$
BEGIN
    ALTER TABLE config.datasource ADD CONSTRAINT ck_ds_layer
        CHECK (layer IN ('p0', 'main', 'base', 'assist'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 排序索引带上 layer：页面按「层级 → seq」分组显示，这是唯一的列表顺序。
-- 老库里这个名字下是 (seq, ds_key)，`IF NOT EXISTS` 不会改它，所以先扔掉。
DROP INDEX IF EXISTS config.ix_datasource_active;
CREATE INDEX IF NOT EXISTS ix_datasource_active
    ON config.datasource (layer, seq, ds_key) WHERE is_active;

-- 探测历史。只留结果快照不够 —— "昨天还好今天坏了"和"一直是坏的"要区别对待，
-- 前者是刚出的故障该立刻处理，后者是已知缺口。
CREATE TABLE IF NOT EXISTS ops.datasource_probe (
    probe_id   bigserial PRIMARY KEY,
    ds_key     text NOT NULL,
    checked_at timestamptz NOT NULL DEFAULT now(),
    health     text NOT NULL,
    latency_ms int,
    row_count  int,
    message    text
);
CREATE INDEX IF NOT EXISTS ix_ds_probe_key
    ON ops.datasource_probe (ds_key, checked_at DESC);


-- ---------------------------------------------------------------- ⑮ 界面说明
-- 每个展示 / 维护窗口标题旁那个「说明」的正文。
--
-- 为什么单独一张表、而且**按字段拆开**：
-- 说明原来写在代码里（`app/web/ui.py` 的 `CARD_HELP`）和布局的
-- `config.page_layout.options.note` 里。写代码里的改一个字要改代码 + 重启；
-- 塞在 options 里的是一整段自由文本，"这张卡的数据源"和"得分口径"混在一段
-- 字符串里，既没法查（"哪些卡用了 fct.score？"）也没法统一格式。
--
-- 拆成字段之后，说明是**组合出来的**：
--   purpose  → 为什么有这个窗口（不写"显示 XX"，写它回答什么问题）
--   content  → 看什么、怎么读
--   取数段   → source_query（命名查询）/ source_tables（库表）/ script（脚本）
--   derive   → 二次加工链：上游哪些数据、谁算的
--   formula  → 参数与得分口径（每个分项什么含义、取值范围）
--   caveat   → 坑与已知缺口（渲染时自动加 ⚠️）
-- 六段按固定顺序拼成 markdown，全站结构一致，缺哪段就少哪段。
--
-- 两种复用：
--   includes  引用 scope='snippet' 的公共片段（如"操作分 = 0.40·X + 0.60·Y"），
--             一处改、所有引用它的说明跟着变；片段可以再引片段，带环检测
--   params    `{名字}` 占位符的取值。窗口天数、权重这类会变的数从这里取，
--             不写死在正文里 —— 写死的数字迟早和代码对不上
--
-- scope / target 的约定（`app/web/help.py` 按这个查）：
--   page     整页说明，target = 路由（'/sector'）
--   card     卡片说明，target = card_ref（配置卡）或页面里手写卡的稳定别名
--   stat     指标块，target = 指标 key（'k_mood'）
--   snippet  只被别人引用、自己不直接显示，target 留空
CREATE TABLE IF NOT EXISTS config.help_note (
    note_key      text PRIMARY KEY,
    scope         text NOT NULL,
    target        text,
    title         text,
    purpose       text,
    content       text,
    source_query  text,
    source_tables text[] NOT NULL DEFAULT '{}',
    script        text,
    derive        text,
    formula       text,
    caveat        text,
    includes      text[] NOT NULL DEFAULT '{}',
    params        jsonb  NOT NULL DEFAULT '{}',
    seq           int    NOT NULL DEFAULT 100,
    enabled       boolean NOT NULL DEFAULT true,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_help_scope CHECK (scope IN ('page', 'card', 'stat', 'snippet')),
    -- snippet 之外都必须能定位到一个窗口，否则这条说明永远不会被显示到
    CONSTRAINT ck_help_target CHECK (scope = 'snippet' OR target IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_help_note_target
    ON config.help_note (scope, target) WHERE enabled;


