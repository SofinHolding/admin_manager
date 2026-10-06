/**
 * Nhóm kênh — người xem tự quản lý nhóm kênh của chính mình (tạo/đổi tên/xóa nhóm,
 * thêm/gỡ kênh kèm nhãn nền tảng và quốc gia ghi đè).
 *
 * Lợi ích nói bằng lời: gom nhiều kênh thành một nhóm để xem tổng riêng và so sánh trên Tổng quan.
 * Kênh khả dụng lấy từ analyticsApi.topChannels (cùng nguồn dữ liệu Dashboard đang dùng).
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Globe, Plus, Save, Trash2, Users, X } from "lucide-react";
import { analyticsApi, groupsApi, type TopChannel, type ViewerGroup } from "../api";
import { daysAgo, defaultFilter } from "../filters/filterState";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";
import { EmptyState, Field, PageHeader, Section } from "../components/kit";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

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
      toast.error(errMsg(e, "Không tải được danh sách nhóm kênh. Kiểm tra mạng rồi tải lại trang."));
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
      toast.success("Đã tạo nhóm");
    } catch (e) {
      toast.error(errMsg(e, "Không tạo được nhóm. Thử lại sau ít phút."));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (group: ViewerGroup) => {
    if (!confirm(`Xóa nhóm "${group.name}"? Các kênh trong nhóm vẫn còn, chỉ nhóm bị xóa. Không hoàn tác được.`)) return;
    try {
      await groupsApi.delete(group.id);
      setGroups((prev) => prev.filter((g) => g.id !== group.id));
      toast.success("Đã xóa nhóm");
    } catch (e) {
      toast.error(errMsg(e, "Không xóa được nhóm. Thử lại sau ít phút."));
    }
  };

  const handleRename = async (group: ViewerGroup, name: string) => {
    if (!name.trim() || name === group.name) return;
    try {
      await groupsApi.rename(group.id, name.trim());
      setGroups((prev) => prev.map((g) => (g.id === group.id ? { ...g, name: name.trim() } : g)));
      toast.success("Đã đổi tên nhóm");
    } catch (e) {
      toast.error(errMsg(e, "Không đổi được tên nhóm"));
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
      toast.error(errMsg(e, "Không thêm được kênh vào nhóm"));
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
      toast.error(errMsg(e, "Không gỡ được kênh khỏi nhóm"));
    }
  };

  return (
    <div>
      <PageHeader
        title="Nhóm kênh"
        description="Gom nhiều kênh thành một nhóm để xem tổng riêng và so sánh với nhóm khác trên trang Tổng quan."
      />

      <Section title="Tạo nhóm mới" description="Ví dụ: “Kênh chính”, “Kênh Nigeria”, “Kênh mới mở”.">
        <form className="flex max-w-xl flex-wrap items-end gap-3" noValidate
              onSubmit={(e) => { e.preventDefault(); void handleCreate(); }}>
          <Field label="Tên nhóm" className="min-w-[14rem] flex-1">
            {(p) => <Input {...p} value={newName} maxLength={100} onChange={(e) => setNewName(e.target.value)} placeholder="Ví dụ: Kênh chính" />}
          </Field>
          <Button type="submit" disabled={creating || !newName.trim()}>
            <Plus aria-hidden /> Tạo nhóm
          </Button>
        </form>
      </Section>

      {loading ? (
        <p className="py-6 text-muted-foreground">Đang tải…</p>
      ) : groups.length === 0 ? (
        <Section>
          <EmptyState icon={Users} title="Bạn chưa có nhóm kênh nào">
            Đặt tên cho nhóm đầu tiên ở trên. Sau khi tạo, bạn chọn các kênh muốn đưa vào nhóm; trang Tổng quan sẽ cho xem tổng và so sánh riêng theo nhóm.
          </EmptyState>
        </Section>
      ) : (
        groups.map((g) => (
          <GroupSection
            key={g.id}
            group={g}
            channels={channels}
            onRename={(name) => handleRename(g, name)}
            onDelete={() => handleDelete(g)}
            onAddMember={(ref, tag, ctry) => handleAddMember(g, ref, tag, ctry)}
            onRemoveMember={(ref) => handleRemoveMember(g, ref)}
          />
        ))
      )}
    </div>
  );
}

function GroupSection({ group, channels, onRename, onDelete, onAddMember, onRemoveMember }: {
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
  const nameOf = (ref: string) => channels.find((c) => c.id === ref)?.name ?? ref;

  return (
    <Section>
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <Field label="Tên nhóm" className="min-w-[12rem] flex-1">
          {(p) => <Input {...p} className="font-semibold" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />}
        </Field>
        {name !== group.name && (
          <Button variant="outline" onClick={() => onRename(name)} disabled={!name.trim()}>
            <Save aria-hidden /> Lưu tên
          </Button>
        )}
        <Button variant="ghost" onClick={onDelete} className="text-destructive" aria-label={`Xóa nhóm ${group.name}`}>
          <Trash2 aria-hidden /> Xóa nhóm
        </Button>
      </div>

      <h3 className="mb-2">Kênh trong nhóm ({group.members.length})</h3>
      {group.members.length === 0 ? (
        <p className="mb-4 text-muted-foreground">Nhóm này chưa có kênh nào. Chọn kênh ở bên dưới để thêm vào.</p>
      ) : (
        <ul className="mb-4 divide-y divide-border border-y border-border">
          {group.members.map((m) => (
            <li key={m.channel_ref} className="flex items-start gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="break-words font-semibold">{nameOf(m.channel_ref)}</p>
                {(m.platform_tag || m.country_override) && (
                  <p className="mt-0.5 flex flex-wrap items-center gap-x-4 gap-y-0.5 text-sm text-muted-foreground">
                    {m.platform_tag && <span>Nhãn nền tảng: {m.platform_tag}</span>}
                    {m.country_override && (
                      <span className="inline-flex items-center gap-1 text-foreground">
                        <Globe className="size-3.5" aria-hidden />
                        Tính vào quốc gia: {m.country_override}
                      </span>
                    )}
                  </p>
                )}
              </div>
              <Button variant="ghost" onClick={() => onRemoveMember(m.channel_ref)} className="shrink-0"
                      aria-label={`Gỡ ${nameOf(m.channel_ref)} khỏi nhóm ${group.name}`}>
                <X aria-hidden /> Gỡ
              </Button>
            </li>
          ))}
        </ul>
      )}

      <form className="max-w-xl space-y-4" noValidate
            onSubmit={(e) => {
              e.preventDefault();
              if (!selectedChannel) return;
              onAddMember(selectedChannel, platformTag, countryOverride);
              setSelectedChannel(""); setPlatformTag(""); setCountryOverride("");
            }}>
        <Field label="Thêm kênh vào nhóm">
          {(p) => (
            <Select value={selectedChannel} onValueChange={setSelectedChannel}>
              <SelectTrigger id={p.id} aria-describedby={p["aria-describedby"]}>
                <SelectValue placeholder="Chọn kênh để thêm" />
              </SelectTrigger>
              <SelectContent>
                {available.length === 0 ? (
                  <div className="px-2 py-1.5 text-sm text-muted-foreground">Không còn kênh nào để thêm</div>
                ) : (
                  available.map((c) => (
                    <SelectItem key={c.id} value={c.id}>{c.name} ({c.platform})</SelectItem>
                  ))
                )}
              </SelectContent>
            </Select>
          )}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Nhãn nền tảng" optional hint="Chỉ để bạn dễ phân biệt, ví dụ: tiktok, instagram.">
            {(p) => <Input {...p} value={platformTag} maxLength={30} onChange={(e) => setPlatformTag(e.target.value)} />}
          </Field>
          <Field label="Quốc gia (ghi đè)" optional
                 hint="Điền nếu muốn tính kênh này vào một quốc gia khác với dữ liệu gốc. Áp dụng ngay cho mọi biểu đồ theo quốc gia.">
            {(p) => <Input {...p} value={countryOverride} maxLength={50} onChange={(e) => setCountryOverride(e.target.value)} placeholder="Ví dụ: Nigeria" />}
          </Field>
        </div>
        <Button type="submit" variant="outline" disabled={!selectedChannel}>
          <Plus aria-hidden /> Thêm kênh vào nhóm
        </Button>
      </form>
    </Section>
  );
}
