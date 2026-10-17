/**
 * Kênh theo người dùng — trang riêng (route /admin/user-channels) cho admin/dash_admin, CHỈ ĐỌC.
 *
 * Liệt kê mọi kênh của từng user trong hệ thống (user nhập ở website hoặc Tool đẩy lên) để đối chiếu với Tool dùng chung:
 * Tool chỉ thấy kênh có chế độ "Tool dùng chung" và chưa xoá, và chỉ liệt kê user còn ≥1 kênh như vậy. Cột "Tool dùng chung thấy" và
 * dòng lý do ở đầu mỗi user cho biết đúng kết quả đó, kể cả khi user bị ẩn vì đã xoá hết kênh (kênh đã xoá hiện mờ, kèm giờ xoá).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, ChevronDown, ChevronRight, MinusCircle, RefreshCw } from "lucide-react";
import { integrationApi, type UserChannel, type UserChannelsResult, type UserChannelsUser } from "../api";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { EmptyState, PageHeader, Section, StatStrip } from "../components/kit";
import { platformLabel, usePlatformNames } from "../lib/platforms";
import { fmtTime, toolReason } from "../lib/userChannels";

type ModeFilter = "all" | "shared" | "personal";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const MODE_LABEL: Record<string, string> = { fallback_tool: "Tool dùng chung", primary_tool: "Tool riêng" };
const ORIGIN_LABEL: Record<string, string> = { web_manual: "Nhập ở website", tool: "Tool đẩy lên" };
const JOB_LABEL: Record<string, string> = {
  pending: "Đang chờ", leased: "Đang chạy", succeeded: "Xong", failed: "Lỗi", dead: "Lỗi hẳn", cancelled: "Đã huỷ",
};

function jobBadge(status: string | null) {
  if (!status) return <span className="text-muted-foreground">—</span>;
  const variant = status === "succeeded" ? "success" : status === "failed" || status === "dead" ? "destructive" : "secondary";
  return <Badge variant={variant}>{JOB_LABEL[status] ?? status}</Badge>;
}

function ChannelRow({ ch, names }: { ch: UserChannel; names: Record<string, string> }) {
  const label = ch.display_name || ch.url || ch.external_id;
  return (
    <tr className={ch.removed ? "text-muted-foreground opacity-70" : undefined}>
      <td className="py-2 pr-3 align-top">{platformLabel(ch.platform, names)}</td>
      <td className="max-w-[26rem] py-2 pr-3 align-top">
        <div className={`truncate font-medium ${ch.removed ? "line-through" : ""}`} title={label}>{label}</div>
        {ch.display_name && ch.external_id !== ch.display_name && (
          <div className="truncate font-mono text-xs text-muted-foreground" title={ch.external_id}>{ch.external_id}</div>
        )}
      </td>
      <td className="py-2 pr-3 align-top">
        <Badge variant={ch.execution_mode === "fallback_tool" ? "default" : "secondary"}>{MODE_LABEL[ch.execution_mode] ?? ch.execution_mode}</Badge>
        <div className="mt-0.5 text-xs text-muted-foreground">{ORIGIN_LABEL[ch.origin] ?? ch.origin}</div>
      </td>
      <td className="py-2 pr-3 align-top">
        {ch.removed ? <Badge variant="destructive">Đã xoá {fmtTime(ch.updated_at)}</Badge>
          : <Badge variant={ch.enabled ? "success" : "secondary"}>{ch.enabled ? "Đang bật" : "Đã tắt"}</Badge>}
      </td>
      <td className="py-2 pr-3 align-top">
        {ch.shared_tool_visible
          ? <span className="inline-flex items-center gap-1 text-success"><CheckCircle2 aria-hidden className="size-4" />Thấy</span>
          : <span className="inline-flex items-center gap-1 text-muted-foreground"><MinusCircle aria-hidden className="size-4" />Không</span>}
      </td>
      <td className="py-2 pr-3 align-top">{jobBadge(ch.last_job_status)}<div className="mt-0.5 text-xs text-muted-foreground">{fmtTime(ch.last_job_at)}</div></td>
      <td className="py-2 pr-3 align-top tabular-nums">
        {ch.content_count} video<div className="mt-0.5 text-xs text-muted-foreground">{fmtTime(ch.last_collected_at)}</div>
      </td>
      <td className="py-2 align-top text-xs text-muted-foreground">{fmtTime(ch.created_at)}</td>
    </tr>
  );
}

function UserBlock({ user, names, open, onToggle, shown }: {
  user: UserChannelsUser; names: Record<string, string>; open: boolean; onToggle: () => void; shown: UserChannel[];
}) {
  const t = user.totals;
  return (
    <li className="py-3">
      <button type="button" onClick={onToggle} aria-expanded={open}
              className="flex w-full cursor-pointer flex-wrap items-center gap-2 text-left">
        {open ? <ChevronDown aria-hidden className="size-4" /> : <ChevronRight aria-hidden className="size-4" />}
        <span className="text-base font-semibold">{user.username}</span>
        <Badge variant={user.visible_in_shared_tool ? "success" : "warning"}>
          {user.visible_in_shared_tool ? "Có ở Tool dùng chung" : "Không ở Tool dùng chung"}
        </Badge>
        <span className="text-sm text-muted-foreground">
          {t.active} kênh · {t.shared_tool} Tool dùng chung · {t.personal_tool} Tool riêng{t.removed > 0 ? ` · ${t.removed} đã xoá` : ""}
        </span>
      </button>
      <p className="ml-6 mt-1 text-sm text-muted-foreground">{toolReason(user)}</p>
      {open && (
        <div className="ml-6 mt-2 overflow-x-auto">
          {shown.length === 0 ? <p className="py-2 text-sm text-muted-foreground">Không có kênh nào khớp bộ lọc.</p> : (
            <table className="w-full min-w-[56rem] text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className="pb-1 pr-3 font-medium">Nền tảng</th><th className="pb-1 pr-3 font-medium">Kênh</th>
                  <th className="pb-1 pr-3 font-medium">Chế độ thu</th><th className="pb-1 pr-3 font-medium">Trạng thái</th>
                  <th className="pb-1 pr-3 font-medium">Tool dùng chung thấy</th><th className="pb-1 pr-3 font-medium">Job gần nhất</th>
                  <th className="pb-1 pr-3 font-medium">Dữ liệu đã thu</th><th className="pb-1 font-medium">Thêm lúc</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {shown.map((ch) => <ChannelRow key={ch.id} ch={ch} names={names} />)}
              </tbody>
            </table>
          )}
        </div>
      )}
    </li>
  );
}

export default function UserChannelsPage() {
  const names = usePlatformNames();
  const [data, setData] = useState<UserChannelsResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<ModeFilter>("all");
  const [showRemoved, setShowRemoved] = useState(true);
  const [onlyHidden, setOnlyHidden] = useState(false);
  const [closed, setClosed] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await integrationApi.userChannels(true));
    } catch (e) {
      toast.error(errMsg(e, "Không tải được danh sách kênh theo người dùng."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const needle = q.trim().toLowerCase();
  const rows = useMemo(() => (data?.users ?? []).flatMap((u) => {
    if (onlyHidden && u.visible_in_shared_tool) return [];
    const userHit = needle !== "" && u.username.toLowerCase().includes(needle);
    const shown = u.channels.filter((c) =>
      (showRemoved || !c.removed)
      && (mode === "all" || (mode === "shared" ? c.execution_mode === "fallback_tool" : c.execution_mode === "primary_tool"))
      && (needle === "" || userHit || [c.display_name, c.external_id, c.url, c.platform].some((s) => s.toLowerCase().includes(needle))));
    // user chỉ bị ẩn khi đang lọc mà không còn kênh nào khớp; không lọc thì user (kể cả user bị ẩn khỏi Tool) luôn hiện
    const filtering = needle !== "" || mode !== "all" || !showRemoved;
    return filtering && shown.length === 0 && !userHit ? [] : [{ user: u, shown }];
  }), [data, needle, mode, showRemoved, onlyHidden]);

  const s = data?.summary;
  const toggle = (id: string) => setClosed((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  return (
    <div>
      <PageHeader
        title="Kênh theo người dùng"
        description="Mọi kênh mà từng người dùng đã thêm vào hệ thống (nhập ở website hoặc do Tool đẩy lên), để đối chiếu với Tool dùng chung. Tool chỉ thấy kênh ở chế độ “Tool dùng chung” chưa xoá — user nào không còn kênh như vậy sẽ không hiện ở Tool."
        actions={<Button variant="outline" onClick={() => void load()} disabled={loading}><RefreshCw aria-hidden className={loading ? "animate-spin" : undefined} /> Tải lại</Button>}
      />
      {s && (
        <StatStrip className="mb-4" stats={[
          { label: "Người dùng có kênh", value: s.users },
          { label: "Hiện ở Tool dùng chung", value: s.visible_in_shared_tool },
          { label: "Kênh đang có", value: s.active_channels },
          { label: "Kênh Tool dùng chung thấy", value: s.shared_tool_channels },
          { label: "Kênh đã xoá", value: s.removed_channels },
        ]} />
      )}
      <Section title="Danh sách" description="Bấm tên user để thu gọn/mở. Kênh đã xoá hiện mờ, gạch ngang, kèm giờ xoá (giờ Việt Nam).">
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm user / kênh / nền tảng" aria-label="Tìm user hoặc kênh" className="w-64" />
          <select value={mode} onChange={(e) => setMode(e.target.value as ModeFilter)} aria-label="Lọc theo chế độ thu"
                  className="h-9 rounded-md border border-border bg-background px-2 text-sm">
            <option value="all">Mọi chế độ thu</option>
            <option value="shared">Tool dùng chung</option>
            <option value="personal">Tool riêng</option>
          </select>
          <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} /> Hiện kênh đã xoá</label>
          <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={onlyHidden} onChange={(e) => setOnlyHidden(e.target.checked)} /> Chỉ user KHÔNG hiện ở Tool</label>
        </div>
        {loading && !data ? <p className="py-6 text-muted-foreground">Đang tải…</p>
          : rows.length === 0 ? <EmptyState title="Không có người dùng nào khớp">Thử bỏ bớt bộ lọc.</EmptyState>
          : (
            <ul className="divide-y divide-border border-y border-border">
              {rows.map(({ user, shown }) => (
                <UserBlock key={user.owner_account_id} user={user} names={names} shown={shown}
                           open={!closed.has(user.owner_account_id)} onToggle={() => toggle(user.owner_account_id)} />
              ))}
            </ul>
          )}
      </Section>
    </div>
  );
}
