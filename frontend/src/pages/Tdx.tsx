import { RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { Contract, Measured, Panel, Row } from "@/components/ui/DataPanel";
import { useNonce, useProbe } from "@/lib/probe";
import { TdxTrail } from "@/components/TdxTrail";
import { dash } from "@/lib/datastate";

/**
 * 通达信控制（规划 §2 维度一）。
 *
 * 2026-10-06 起这一页的前提变了：**盘后数据下载不再只有人手那一条路**。
 * 收盘链 ③c 会自己开「盘后数据下载」、勾日线/分钟线、点开始下载；
 * 下载阶梯每 10 分钟按整目录条数判一次，判全了就关窗、收掉赖着的 `TPyth*`，
 * 两档都没全就全重启客户端再下一遍；用户当天也点了头让系统自己拉起并登录。
 * 所以这一页的职责从"承认不自动"换成**把自动动作摆出来 + 把没做成的那几条交回给人**
 * （`duanxian/gui_trail.py` 那份待人工清单，每条写着人怎么补）。
 *
 * 仍然守着的纪律：**别把没探到的写成结论**。主站连接、后台同步开关这两个在客户端
 * 自绘界面里，后台服务账号读不到，这两格恒为空 —— 不许用"看起来像"的值顶上。
 */

interface TrailItem {
  at?: string | null; day?: string | null; step?: string | null; what?: string | null;
  status?: string | null; detail?: string | null; manual?: string | null;
}

interface TdxStatus {
  available?: boolean | null; reason?: string | null;
  client_running?: boolean | null; logged_in?: boolean | null;
  login_dialog?: boolean | null; sync?: string | null;
  server_connected?: boolean | null; background_sync?: boolean | null;
  not_probed?: string | null;
  tq_ready?: boolean | null; tq_reason?: string | null;
  tq_endpoint?: string | null; tq_busy?: boolean | null;
  last_probe_at?: string | null; vipdoc_dir?: string | null;
  last_file_date?: string | null; expected_data_date?: string | null;
  trail?: { count?: number | null; pending?: TrailItem[] | null; path?: string | null } | null;
}

export function Tdx() {
  const [nonce, refresh] = useNonce();
  const st = useProbe<TdxStatus>("/tdx/status", nonce);

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="通达信控制" level={1}
        subtitle="客户端在不在、登录没有、文件到哪一天，以及系统自己动界面的每一次记录 —— 没点成的那几条列在下面，附人怎么补。"
        actions={<button type="button" onClick={refresh}
          className="flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1.5 text-[12px] font-medium text-primary hover:bg-primary/25">
          <RefreshCw className="h-3.5 w-3.5" /> 重新探测
        </button>} />

      <div className="space-y-4">
        <Panel title="客户端与连接" state={st}
          note="探测结果必须来自进程与窗口的实际状态，不来自「上次跑过」；`logged_in` 的判据是**没停在登录框上**。"
          contract={<Contract path="GET /api/tdx/status" source="现问探测（只枚举窗口，不动界面）" fields={[
            ["client_running", "通达信进程在不在"],
            ["logged_in / login_dialog", "登录态（停在登录框 = 没登录，TQ 取不到数）"],
            ["sync", "`TPyth*` 同步子进程是谁 —— 盘后下载正在进行时它会在"],
            ["tq_ready / tq_reason", "TQ 这条路现在能不能走，走不通是什么原因"],
            ["server_connected / background_sync", "读不到（在客户端自绘界面里）→ 恒为空，不是「没连」也不是「关」"],
            ["vipdoc_dir / last_file_date / expected_data_date", "文件在哪、尾部日期到哪一天、该到哪一天"],
            ["last_probe_at", "这次探测是什么时候做的"],
            ["trail", "系统自己动界面的记录 + 待人工清单（`duanxian/gui_trail.py`）"],
          ]} />}>
          {d => <>
            <Row label="进程" value={d.client_running === true ? "在跑" : d.client_running === false ? "没在跑" : "—"} tone={d.client_running === false ? "bad" : undefined} />
            <Row label="已登录" value={d.logged_in === true ? "是" : d.login_dialog === true ? "否（停在登录框）" : d.logged_in === false ? "否" : "—"} tone={d.logged_in === false ? "warn" : undefined} />
            <Row label="同步子进程" value={dash(d.sync)} />
            <Row label="主站连接" value={d.server_connected === true ? "已连上" : d.server_connected === false ? "未连上" : "读不到（不是「没连」）"} />
            <Row label="后台同步开关" value={d.background_sync === true ? "开" : d.background_sync === false ? "关" : "读不到（不是「关」）"} />
            <Row label="TQ 能不能走" value={d.tq_ready === true ? "能" : d.tq_ready === false ? `不能：${dash(d.tq_reason)}` : "—"} tone={d.tq_ready === false ? "warn" : undefined} />
            <Row label="vipdoc 目录" value={dash(d.vipdoc_dir)} />
            <Row label="文件数据日期" value={`${dash(d.last_file_date)}（应在 ${dash(d.expected_data_date)}）`} />
            <Row label="本次探测于" value={dash(d.last_probe_at)} />
            {d.not_probed && <Row label="为什么有两格恒为空" value={d.not_probed} />}
            {d.trail && <TdxTrail count={d.trail.count} pending={d.trail.pending} path={d.trail.path} />}
          </>}
        </Panel>

        <GlassCard>
          <h2 className="text-sm font-bold">自动动作的边界在哪</h2>
          <Measured>
            这一页没有"一键启动通达信"的按钮，是因为<b className="text-foreground">不需要按钮</b>：
            15:59 收盘链到点会自己判数据、自己拉起并登录、自己开下载窗勾线；
            下载阶梯每 10 分钟按<b className="text-foreground">整目录条数</b>判一次
            （判全就关窗收进程，两档都没全就全重启客户端再下一遍）。
            还守着的边界有两条：① <b className="text-foreground">绝不动主客户端进程</b>
            —— 只收赖着的 `TPyth*`，TQ 那条腿要 `TdxW.exe` 登录着；
            ② <b className="text-foreground">有链在跑时阶梯不接管</b> —— 两双手抢一个客户端必然互相取消。
            要收回去就在系统配置把 `tdx_watch.auto_launch` / `auto_restart` 关掉；
            关掉之后剩下的那一下确实是鼠标点的，<b className="text-foreground">没人点就是零</b>，
            这一页不会替它遮羞。
          </Measured>
        </GlassCard>
      </div>

      <Disclaimer compact />
    </div>
  );
}
