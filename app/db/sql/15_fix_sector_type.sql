-- =============================================================================
-- 15 · 修正 reference_data.sector_info 里 37 个板块的 sector_type 错标
--
-- 成因（实测 2026-09-24）：c17_tdx_sector.py:116 的类型列是**保守补空**
--   `sector_type = COALESCE(现值, EXCLUDED.sector_type)` —— 只把 NULL 填上，永不纠正已有值。
-- 于是更早由一次性搬迁脚本 b7_sector_key.py:191,217（已归档，位置见
-- 《数据加工工作指南/06-数据清洗与加工任务总览》）或 `app/db/repo/security.py:204`
-- 写成 '行业' 的 37 个板块，c17 再跑多少次都改不回来。
-- 唯一权威源是通达信自己的 `T0002/hq_cache/tdxzs*.cfg`（解析器已搬进
-- YuanShan/ingest/tdx_sector_cfg.py，与 c17 逐字节对过：1071 个板块完全一致）。
--
-- 影响面（不修就是错数据在下游扩散）：qd.sector_rotation 有 311 行、
-- **275 个交易日**的领涨前 8 里混着这 37 个板块（TDX_880206 青海拿过 #1 共 62 次、
-- TDX_880854 预高送转 #1、TDX_880214 宁夏 #1）。而 09 §4.3 明写 quantdash 的板块宇宙
-- "仅概念板块 + 行业板块，**无地域板块**" —— 也就是说这 275 天的 current_leader_name /
-- streak_days / top_three_appearances 是在**违反契约的宇宙**上算出来的。
--
-- 本文件**尚未应用**：它是对共享参考表的 UPDATE，且 P0 备份恢复验证还没过。
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS operations.sector_type_fix_20260924 (
    sector_code text PRIMARY KEY, name text, old_type text, new_type text,
    wrote_at timestamptz NOT NULL DEFAULT now()
);

-- 先把现值原样存一份，出问题可以逐行回滚回去（这张表就是本次变更的回滚凭据）
INSERT INTO operations.sector_type_fix_20260924 (sector_code, name, old_type, new_type)
VALUES
    ('TDX_880201', '黑龙江', '行业', '地区'),
    ('TDX_880202', '新疆板块', '行业', '地区'),
    ('TDX_880203', '吉林板块', '行业', '地区'),
    ('TDX_880204', '甘肃板块', '行业', '地区'),
    ('TDX_880205', '辽宁板块', '行业', '地区'),
    ('TDX_880206', '青海板块', '行业', '地区'),
    ('TDX_880207', '北京板块', '行业', '地区'),
    ('TDX_880208', '陕西板块', '行业', '地区'),
    ('TDX_880209', '天津板块', '行业', '地区'),
    ('TDX_880210', '广西板块', '行业', '地区'),
    ('TDX_880211', '河北板块', '行业', '地区'),
    ('TDX_880212', '广东板块', '行业', '地区'),
    ('TDX_880213', '河南板块', '行业', '地区'),
    ('TDX_880214', '宁夏板块', '行业', '地区'),
    ('TDX_880215', '山东板块', '行业', '地区'),
    ('TDX_880216', '上海板块', '行业', '地区'),
    ('TDX_880217', '山西板块', '行业', '地区'),
    ('TDX_880218', '深圳板块', '行业', '地区'),
    ('TDX_880219', '湖北板块', '行业', '地区'),
    ('TDX_880220', '福建板块', '行业', '地区'),
    ('TDX_880221', '湖南板块', '行业', '地区'),
    ('TDX_880222', '江西板块', '行业', '地区'),
    ('TDX_880223', '四川板块', '行业', '地区'),
    ('TDX_880224', '安徽板块', '行业', '地区'),
    ('TDX_880225', '重庆板块', '行业', '地区'),
    ('TDX_880226', '江苏板块', '行业', '地区'),
    ('TDX_880227', '云南板块', '行业', '地区'),
    ('TDX_880228', '浙江板块', '行业', '地区'),
    ('TDX_880229', '贵州板块', '行业', '地区'),
    ('TDX_880230', '海南板块', '行业', '地区'),
    ('TDX_880231', '西藏板块', '行业', '地区'),
    ('TDX_880232', '内蒙板块', '行业', '地区'),
    ('TDX_880524', '含可转债', '行业', '概念'),
    ('TDX_880735', '专精特新', '行业', '风格'),
    ('TDX_880851', '已高送转', '行业', '风格'),
    ('TDX_880854', '预高送转', '行业', '风格'),
    ('TDX_880890', '配股预案', '行业', '风格')
ON CONFLICT (sector_code) DO UPDATE SET old_type = EXCLUDED.old_type,
    new_type = EXCLUDED.new_type, wrote_at = now();

-- 生效的更新（只动上面登记的那 37 个主键；IS DISTINCT 保证幂等）：
UPDATE reference_data.sector_info i
   SET sector_type = f.new_type
  FROM operations.sector_type_fix_20260924 f
 WHERE i.sector_code = f.sector_code
   AND i.sector_type IS DISTINCT FROM f.new_type;

-- 应用后必须重跑板块轨，否则 qd.sector_rotation 里那 311 行仍是旧宇宙算的：
--   python -m ingest.jobs.qd3_sector --from 2021-12-01
ROLLBACK;   -- ⚠️ 本文件默认只演练。要真改，删掉这一行再跑。
