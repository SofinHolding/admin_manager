/**
 * Trang Nhóm kênh — viewer tự quản lý nhóm kênh của chính mình (tạo/đổi tên/xoá group,
 * thêm/xoá kênh vào group kèm nhãn nền tảng tự do).
 *
 * Kênh khả dụng lấy từ analyticsApi.topChannels (cùng nguồn dữ liệu Dashboard đang dùng).
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Plus, Save, Trash2, Users, X } from "lucide-react";
import { analyticsApi, groupsApi, type TopChannel, type ViewerGroup } from "../api";
import { daysAgo, defaultFilter } from "../filters/filterState";
import { Button } from "../components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const inputCls =
  "h-9 w-full rounded-md border border-border bg-elev-1 px-3 text-sm outline-none transition-colors focus:border-primary";

export default function GroupsPage() {
  const [groups, setGroups] = useState<ViewerGroup[]>([]);
  const [channels, setChannels] = useState<TopChannel[]>([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { items } = await groupsApi.list();
      setGroups(items);
    } catch (e) {
      toast.error(errMsg(e, "Không tải được danh sách nhóm kênh"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const ctl = new AbortController();
    analyticsApi.topChannels({ ...defaultFilter(), date_from: daysAgo(89) }, 100, { signal: ctl.signal })
      .then((d) => setChannels(d.items))
      .catch(() => { if (!ctl.signal.aborted) setChannels([]); });
    return () => ctl.abort();
  }, []);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const group = await groupsApi.create(newName.trim());
      setGroups((prev) => [{ ...group, members: group.members ?? [] }, ...prev]);
      setNewName("");
      toast.success("Đã tạo nhóm kênh");
    } catch (e) {
      toast.error(errMsg(e, "Tạo nhóm thất bại"));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (group: ViewerGroup) => {
    if (!confirm(`Xoá nhóm "${group.name}"? Hành động này không thể hoàn tác.`)) return;
    try {
      await groupsApi.delete(group.id);
      setGroups((prev) => prev.filter((g) => g.id !== group.id));
      toast.success("Đã xoá nhóm kênh");
    } catch (e) {
      toast.error(errMsg(e, "Xoá nhóm thất bại"));
    }
  };

  const handleRename = async (group: ViewerGroup, name: string) => {
    if (!name.trim() || name === group.name) return;
    try {
      await groupsApi.rename(group.id, name.trim());
      setGroups((prev) => prev.map((g) => (g.id === group.id ? { ...g, name: name.trim() } : g)));
      toast.success("Đã đổi tên nhóm");
    } catch (e) {
      toast.error(errMsg(e, "Đổi tên thất bại"));
    }
  };

  const handleAddMember = async (
    group: ViewerGroup, channelRef: string, platformTag: string, countryOverride: string,
  ) => {
    if (!channelRef) return;
    try {
      await groupsApi.addMember(group.id, {
        channel_ref: channelRef, platform_tag: platformTag, country_override: countryOverride,
      });
      const { items } = await groupsApi.list();
      setGroups(items);
      toast.success("Đã thêm kênh vào nhóm");
    } catch (e) {
      toast.error(errMsg(e, "Thêm kênh thất bại"));
    }
  };

  const handleRemoveMember = async (group: ViewerGroup, channelRef: string) => {
    try {
      await groupsApi.removeMember(group.id, channelRef);
      setGroups((prev) =>
        prev.map((g) =>
          g.id === group.id ? { ...g, members: g.members.filter((m) => m.channel_ref !== channelRef) } : g,
        ),
      );
      toast.success("Đã gỡ kênh khỏi nhóm");
    } catch (e) {
      toast.error(errMsg(e, "Gỡ kênh thất bại"));
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="flex items-center gap-2 text-xl font-bold">
        <Users className="size-5" /> Nhóm kênh
      </h1>

      <div className="rounded-xl border border-border bg-elev-1 p-4">
        <h3 className="mb-3 text-sm font-semibold">Tạo nhóm mới</h3>
        <div className="flex items-end gap-3">
          <div className="flex-1">
            <label className="mb-1 block text-xs text-muted-foreground">Tên nhóm</label>
            <input
              type="text"
              className={inputCls}
              placeholder="VD: Kênh chính"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              maxLength={100}
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            />
          </div>
          <Button onClick={handleCreate} disabled={creating || !newName.trim()} className="h-9 gap-1.5">
            <Plus className="size-4" /> Tạo nhóm
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="p-6 text-center text-sm text-muted-foreground">Đang tải…</div>
      ) : groups.length === 0 ? (
        <div className="rounded-xl border border-border bg-elev-1 p-6 text-center text-sm text-muted-foreground">
          Chưa có nhóm kênh nào. Tạo nhóm đầu tiên ở trên.
        </div>
      ) : (
        <div className="space-y-4">
          {groups.map((g) => (
            <GroupCard
              key={g.id}
              group={g}
              channels={channels}
              onRename={(name) => handleRename(g, name)}
              onDelete={() => handleDelete(g)}
              onAddMember={(ref, tag, ctry) => handleAddMember(g, ref, tag, ctry)}
              onRemoveMember={(ref) => handleRemoveMember(g, ref)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function GroupCard({ group, channels, onRename, onDelete, onAddMember, onRemoveMember }: {
  group: ViewerGroup;
  channels: TopChannel[];
  onRename: (name: string) => void;
  onDelete: () => void;
  onAddMember: (channelRef: string, platformTag: string, countryOverride: string) => void;
  onRemoveMember: (channelRef: string) => void;
}) {
  const [name, setName] = useState(group.name);
  const [selectedChannel, setSelectedChannel] = useState("");
  const [platformTag, setPlatformTag] = useState("");
  const [countryOverride, setCountryOverride] = useState("");
  const memberRefs = new Set(group.members.map((m) => m.channel_ref));
  const available = channels.filter((c) => !memberRefs.has(c.id));

  return (
    <div className="rounded-xl border border-border bg-elev-1 p-4">
      <div className="mb-3 flex items-center gap-2">
        <input
          type="text"
          className={`${inputCls} flex-1 font-medium`}
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={100}
        />
        {name !== group.name && (
          <Button variant="ghost" size="sm" onClick={() => onRename(name)} title="Lưu tên">
            <Save className="size-4" />
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={onDelete} className="text-red-500 hover:text-red-600" title="Xoá nhóm">
          <Trash2 className="size-4" />
        </Button>
      </div>

      <div className="mb-3 space-y-2">
        {group.members.length === 0 ? (
          <p className="text-sm text-muted-foreground">Chưa có kênh nào trong nhóm.</p>
        ) : (
          group.members.map((m) => (
            <div
              key={m.channel_ref}
              className="flex items-center justify-between rounded-md border border-border/60 px-3 py-2 text-sm"
            >
              <div>
                <span className="font-medium">{m.channel_ref}</span>
                {m.platform_tag && (
                  <span className="ml-2 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary-text">
                    {m.platform_tag}
                  </span>
                )}
                {m.country_override && (
                  <span className="ml-2 rounded-full bg-amber-500/10 px-2 py-0.5 text-xs text-amber-600"
                        title="Quốc gia đã ghi đè — áp dụng cho mọi biểu đồ tính theo quốc gia">
                    🌐 {m.country_override}
                  </span>
                )}
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onRemoveMember(m.channel_ref)}
                className="text-red-500 hover:text-red-600"
                title="Gỡ khỏi nhóm"
              >
                <X className="size-4" />
              </Button>
            </div>
          ))
        )}
      </div>

      <div className="flex flex-wrap items-end gap-2 border-t border-border pt-3">
        <div className="min-w-[220px] flex-1">
          <label className="mb-1 block text-xs text-muted-foreground">Chọn kênh</label>
          <Select value={selectedChannel} onValueChange={setSelectedChannel}>
            <SelectTrigger className="h-9">
              <SelectValue placeholder="Chọn kênh để thêm" />
            </SelectTrigger>
            <SelectContent>
              {available.length === 0 ? (
                <div className="px-2 py-1.5 text-xs text-muted-foreground">Không còn kênh nào để thêm</div>
              ) : (
                available.map((c) => (
                  <SelectItem key={c.id} value={c.id}>{c.name} ({c.platform})</SelectItem>
                ))
              )}
            </SelectContent>
          </Select>
        </div>
        <div className="w-40">
          <label className="mb-1 block text-xs text-muted-foreground">Nhãn nền tảng</label>
          <input
            type="text"
            className={inputCls}
            placeholder="tiktok, instagram…"
            value={platformTag}
            onChange={(e) => setPlatformTag(e.target.value)}
            maxLength={30}
          />
        </div>
        <div className="w-40">
          <label className="mb-1 block text-xs text-muted-foreground">Quốc gia (ghi đè)</label>
          <input
            type="text"
            className={inputCls}
            placeholder="VD: Nigeria"
            value={countryOverride}
            onChange={(e) => setCountryOverride(e.target.value)}
            maxLength={50}
            title="Sửa quốc gia của kênh này — áp dụng ngay cho mọi biểu đồ theo quốc gia, đồng bộ với nhãn nền tảng"
          />
        </div>
        <Button
          size="sm"
          onClick={() => {
            onAddMember(selectedChannel, platformTag, countryOverride);
            setSelectedChannel(""); setPlatformTag(""); setCountryOverride("");
          }}
          disabled={!selectedChannel}
          className="h-9 gap-1.5"
        >
          <Plus className="size-4" /> Thêm kênh
        </Button>
      </div>
    </div>
  );
}
