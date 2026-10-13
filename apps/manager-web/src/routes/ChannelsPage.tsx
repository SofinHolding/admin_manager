/**
 * Kênh của tôi (v2) — viewer tự quản lý nhóm quốc gia và kênh trong từng nhóm.
 *
 * Layout:
 *  - Tạo nhóm quốc gia mới (form inline)
 *  - Accordion per group: tên nước + mã ISO + danh sách kênh + form thêm kênh
 *  - Kênh: nền tảng + username/link + tên hiển thị + badge trạng thái + nút xóa
 *
 * Backend:
 *  - Nhóm: /v1/user/channel-groups (integration-service migration 0007)
 *  - Kênh: /v1/resources (integration-service) — country = group.name để liên kết
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  ChevronDown, ChevronUp, Globe, Pencil, Plus, RefreshCw, Save, Trash2, X,
} from "lucide-react";
import {
  channelGroupsApi, resourcesApi,
  type ChannelGroup, type ManagedResource,
} from "../api";
import { errorMessage } from "../lib/errors";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";
import { Badge } from "../components/ui/badge";
import { EmptyState, Field, Notice, PageHeader, Section } from "../components/kit";

// ── Hằng số ──────────────────────────────────────────────────────────────────

const PLATFORM_LABEL: Record<string, string> = {
  youtube: "YouTube",
  tiktok: "TikTok",
  facebook: "Facebook",
};

const PLATFORM_EMOJI: Record<string, string> = {
  youtube: "📹",
  tiktok: "🎵",
  facebook: "👤",
};

const VERIFICATION_BADGE: Record<string, { text: string; variant: "secondary" | "success" | "destructive" | "warning" }> = {
  unverified: { text: "Chưa thu thập", variant: "secondary" },
  valid:      { text: "Hợp lệ",        variant: "success" },
  invalid:    { text: "Lỗi",           variant: "destructive" },
};

/** Nhãn + màu badge theo chế độ thu thập. */
const MODE_BADGE: Record<string, { text: string; hint: string; variant: "secondary" | "warning" | "success" | "destructive" }> = {
  fallback_local: { text: "Máy tập trung", hint: "Dữ liệu được máy chủ tập trung tự kéo về",        variant: "secondary" },
  primary_tool:   { text: "Tool riêng",    hint: "Dữ liệu do Tool cá nhân của bạn đẩy lên",          variant: "success"   },
  disabled:       { text: "Tắt",           hint: "Kênh này tạm thời không thu thập dữ liệu nào",     variant: "destructive" },
};

/** Danh sách quốc gia phổ biến cho datalist gợi ý. */
const COMMON_COUNTRIES: [string, string][] = [
  ["VN", "Việt Nam"], ["NG", "Nigeria"], ["US", "United States"],
  ["PH", "Philippines"], ["ID", "Indonesia"], ["IN", "India"],
  ["BR", "Brazil"], ["MX", "Mexico"], ["EG", "Egypt"], ["KE", "Kenya"],
];

// ── Sub-components ────────────────────────────────────────────────────────────

function VerBadge({ r }: { r: ManagedResource }) {
  const spec = VERIFICATION_BADGE[r.verification_status] ?? { text: r.verification_status, variant: "secondary" as const };
  return <Badge variant={spec.variant}>{spec.text}</Badge>;
}

function ModeBadge({ r, onSwitch }: { r: ManagedResource; onSwitch: (mode: string) => void }) {
  const spec = MODE_BADGE[r.execution_mode] ?? { text: r.execution_mode, variant: "secondary" as const, hint: "" };
  return (
    <span className="flex items-center gap-1.5">
      <Badge variant={spec.variant} title={spec.hint}>{spec.text}</Badge>
      {r.execution_mode !== "disabled" && (
        <button
          type="button"
          title={r.execution_mode === "fallback_local" ? "Chuyển sang Tool riêng" : "Chuyển sang máy tập trung"}
          className="rounded p-0.5 text-muted-foreground hover:text-fg"
          onClick={() => onSwitch(r.execution_mode === "fallback_local" ? "primary_tool" : "fallback_local")}
        >
          <RefreshCw className="size-3" aria-hidden />
        </button>
      )}
    </span>
  );
}

// ── AddChannelForm ─────────────────────────────────────────────────────────────

function AddChannelForm({
  group, platforms, onAdded,
}: {
  group: ChannelGroup;
  platforms: string[];
  onAdded: (r: ManagedResource) => void;
}) {
  const [platform, setPlatform] = useState(platforms[0] ?? "youtube");
  const [inputVal, setInputVal] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [saving, setSaving] = useState(false);

  const handleAdd = async () => {
    if (!platform || !inputVal.trim()) return;
    setSaving(true);
    try {
      // Tìm hoặc tạo source cho platform + nhóm này
      const sources = await resourcesApi.listSources();
      const source =
        sources.items.find((s) => s.platform === platform && (s.project_key ?? "") === group.name)
        ?? sources.items.find((s) => s.platform === platform)
        ?? await resourcesApi.createSource({ name: group.name, platform, default_country: group.name });

      const created = await resourcesApi.createResource({
        source_instance_id: source.id,
        platform,
        input_ref: inputVal.trim(),
        display_name: displayName.trim() || undefined,
        country: group.name,  // liên kết với group
      });
      onAdded(created);
      setInputVal("");
      setDisplayName("");
      toast.success("Đã thêm kênh vào nhóm " + group.name);
    } catch (e) {
      toast.error(errorMessage(e, "Không thêm được kênh. Hãy thử lại."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form
      className="mt-4 grid gap-3 rounded-lg border border-dashed border-border p-4 sm:grid-cols-[1fr_1fr_auto]"
      onSubmit={(e) => { e.preventDefault(); void handleAdd(); }}
    >
      <Field label="Nền tảng">
        {(p) => (
          <Select value={platform} onValueChange={setPlatform}>
            <SelectTrigger {...p}><SelectValue /></SelectTrigger>
            <SelectContent>
              {platforms.map((pl) => (
                <SelectItem key={pl} value={pl}>
                  {PLATFORM_EMOJI[pl] ?? ""} {PLATFORM_LABEL[pl] ?? pl}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </Field>
      <Field label="Username hoặc link kênh" hint="Ví dụ: @mykênh hoặc https://fb.com/page">
        {(p) => (
          <Input
            {...p}
            value={inputVal}
            onChange={(e) => setInputVal(e.target.value)}
            placeholder="@username hoặc link"
          />
        )}
      </Field>
      <Field label="Tên hiển thị" optional>
        {(p) => (
          <Input
            {...p}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Tên dễ nhớ (tùy chọn)"
          />
        )}
      </Field>
      <div className="flex items-end sm:col-span-3">
        <Button type="submit" disabled={saving || !inputVal.trim()} size="sm">
          <Plus aria-hidden /> {saving ? "Đang thêm…" : "Thêm kênh"}
        </Button>
      </div>
    </form>
  );
}

// ── GroupSection (accordion item) ─────────────────────────────────────────────

function GroupSection({
  group, platforms, resources,
  onRename, onDelete, onChannelAdded, onChannelDeleted, onChannelModeChanged,
}: {
  group: ChannelGroup;
  platforms: string[];
  resources: ManagedResource[];
  onRename: (id: string, name: string, code: string) => void;
  onDelete: (id: string) => void;
  onChannelAdded: (r: ManagedResource) => void;
  onChannelDeleted: (id: string) => void;
  onChannelModeChanged: (id: string, mode: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(group.name);
  const [editCode, setEditCode] = useState(group.country_code);
  const [renaming, setRenaming] = useState(false);
  const [deletingChannelId, setDeletingChannelId] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const channels = resources.filter((r) => r.country === group.name);

  const handleRename = async () => {
    if (!editName.trim() || (editName === group.name && editCode === group.country_code)) {
      setEditing(false);
      return;
    }
    setRenaming(true);
    try {
      await channelGroupsApi.update(group.id, { name: editName.trim(), country_code: editCode.trim() });
      onRename(group.id, editName.trim(), editCode.trim());
      setEditing(false);
      toast.success("Đã cập nhật tên nhóm");
    } catch (e) {
      toast.error(errorMessage(e, "Không đổi được tên nhóm"));
    } finally {
      setRenaming(false);
    }
  };

  const handleDeleteGroup = async () => {
    if (!confirm(`Xóa nhóm "${group.name}"?\nCác kênh trong nhóm vẫn còn nhưng sẽ không thuộc nhóm nào. Không hoàn tác được.`)) return;
    try {
      await channelGroupsApi.delete(group.id);
      onDelete(group.id);
      toast.success("Đã xóa nhóm " + group.name);
    } catch (e) {
      toast.error(errorMessage(e, "Không xóa được nhóm"));
    }
  };

  const handleDeleteChannel = async (r: ManagedResource) => {
    const label = r.display_name || r.username || r.input_ref;
    setDeletingChannelId(r.id);
    try {
      await resourcesApi.deleteResource(r.id);
      onChannelDeleted(r.id);
      toast.success("Đã xóa kênh " + label);
    } catch (e) {
      toast.error(errorMessage(e, "Không xóa được kênh"));
    } finally {
      setDeletingChannelId(null);
    }
  };

  const handleSwitchMode = async (r: ManagedResource, newMode: string) => {
    const label = r.display_name || r.username || r.input_ref;
    try {
      await resourcesApi.updateResourceMode(r.id, newMode);
      onChannelModeChanged(r.id, newMode);
      const modeLabel = MODE_BADGE[newMode]?.text ?? newMode;
      toast.success(`Kênh "${label}" chuyển sang: ${modeLabel}`);
    } catch (e) {
      toast.error(errorMessage(e, "Không chuyển được chế độ thu thập"));
    }
  };

  return (
    <div className="overflow-hidden rounded-lg border border-border">
      {/* Header */}
      <div className="flex items-center gap-2 bg-surface-2 px-4 py-3">
        <button
          type="button"
          className="flex flex-1 items-center gap-2 text-left font-semibold"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
        >
          <Globe className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          {editing ? (
            <span className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
              <Input
                ref={nameRef}
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="h-7 w-40 px-2 py-0 text-sm"
                placeholder="Tên nhóm"
                onKeyDown={(e) => { if (e.key === "Enter") void handleRename(); if (e.key === "Escape") setEditing(false); }}
              />
              <Input
                value={editCode}
                onChange={(e) => setEditCode(e.target.value.toUpperCase().slice(0, 10))}
                className="h-7 w-16 px-2 py-0 font-mono text-sm"
                placeholder="VN"
                maxLength={10}
                onKeyDown={(e) => { if (e.key === "Enter") void handleRename(); }}
              />
            </span>
          ) : (
            <span className="flex items-center gap-2">
              {group.name}
              {group.country_code && (
                <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                  {group.country_code}
                </span>
              )}
              <span className="text-sm font-normal text-muted-foreground">
                {channels.length} kênh
              </span>
            </span>
          )}
          {open ? <ChevronUp className="ml-auto size-4 shrink-0 text-muted-foreground" aria-hidden /> : <ChevronDown className="ml-auto size-4 shrink-0 text-muted-foreground" aria-hidden />}
        </button>

        {/* Actions */}
        {editing ? (
          <span className="flex shrink-0 items-center gap-1">
            <Button variant="outline" size="sm" onClick={handleRename} disabled={renaming}>
              <Save aria-hidden className="size-4" /> {renaming ? "Đang lưu…" : "Lưu"}
            </Button>
            <Button variant="outline" size="sm" onClick={() => { setEditing(false); setEditName(group.name); setEditCode(group.country_code); }}>
              <X aria-hidden className="size-4" />
            </Button>
          </span>
        ) : (
          <span className="flex shrink-0 items-center gap-1">
            <Button variant="outline" size="sm" onClick={() => { setEditing(true); setTimeout(() => nameRef.current?.focus(), 50); }}
              aria-label={`Đổi tên nhóm ${group.name}`}>
              <Pencil aria-hidden className="size-4" />
            </Button>
            <Button variant="outline" size="sm" onClick={handleDeleteGroup}
              aria-label={`Xóa nhóm ${group.name}`}>
              <Trash2 aria-hidden className="size-4 text-destructive" />
            </Button>
          </span>
        )}
      </div>

      {/* Body */}
      {open && (
        <div className="divide-y divide-border px-4 pb-4">
          {channels.length === 0 ? (
            <p className="py-4 text-sm text-muted-foreground">Chưa có kênh nào trong nhóm này.</p>
          ) : (
            <ul role="list" className="divide-y divide-border">
              {channels.map((r) => {
                const label = r.display_name || r.username || r.input_ref;
                return (
                  <li key={r.id} className="flex min-h-12 items-center gap-3 py-2">
                    <span className="text-lg" aria-label={PLATFORM_LABEL[r.platform] ?? r.platform}>
                      {PLATFORM_EMOJI[r.platform] ?? "🔗"}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{label}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {PLATFORM_LABEL[r.platform] ?? r.platform}
                        {r.username ? ` · @${r.username}` : ""}
                        {r.url ? ` · ${r.url}` : ""}
                      </p>
                    </div>
                    <span className="flex shrink-0 items-center gap-2">
                      <VerBadge r={r} />
                      <ModeBadge r={r} onSwitch={(mode) => void handleSwitchMode(r, mode)} />
                      <Button
                        variant="outline" size="sm"
                        onClick={() => void handleDeleteChannel(r)}
                        disabled={deletingChannelId === r.id}
                        aria-label={`Xóa kênh ${label}`}
                      >
                        <Trash2 aria-hidden className="size-4" />
                        {deletingChannelId === r.id ? "Đang xóa…" : "Xóa"}
                      </Button>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          <AddChannelForm group={group} platforms={platforms} onAdded={onChannelAdded} />
        </div>
      )}
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function ChannelsPage() {
  const [groups, setGroups] = useState<ChannelGroup[]>([]);
  const [resources, setResources] = useState<ManagedResource[]>([]);
  const [platforms, setPlatforms] = useState<string[]>(["youtube", "tiktok", "facebook"]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Tạo nhóm mới
  const [newName, setNewName] = useState("");
  const [newCode, setNewCode] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [{ items: grps }, { items: res }, plat] = await Promise.all([
        channelGroupsApi.list(),
        resourcesApi.listResources(),
        resourcesApi.listPlatforms(),
      ]);
      setGroups(grps);
      setResources(res);
      const names = plat.items.map((p) => p.platform);
      if (names.length) setPlatforms(names);
      setLoadError(null);
    } catch (e) {
      setLoadError(errorMessage(e, "Không tải được dữ liệu. Kiểm tra mạng rồi thử lại."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const handleCreateGroup = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const group = await channelGroupsApi.create(newName.trim(), newCode.trim().toUpperCase());
      setGroups((prev) => [...prev, group]);
      setNewName("");
      setNewCode("");
      toast.success(`Đã tạo nhóm "${group.name}"`);
    } catch (e) {
      toast.error(errorMessage(e, "Không tạo được nhóm. Tên có thể đã tồn tại."));
    } finally {
      setCreating(false);
    }
  };

  const handleRename = (id: string, name: string, code: string) => {
    setGroups((prev) => prev.map((g) => g.id === id ? { ...g, name, country_code: code } : g));
    // Cập nhật country trên resources để vẫn liên kết đúng
    setResources((prev) => {
      const old = groups.find((g) => g.id === id);
      if (!old) return prev;
      return prev.map((r) => r.country === old.name ? { ...r, country: name } : r);
    });
  };

  const handleDeleteGroup = (id: string) => {
    setGroups((prev) => prev.filter((g) => g.id !== id));
  };

  const handleChannelAdded = (r: ManagedResource) => {
    setResources((prev) => [r, ...prev]);
  };

  const handleChannelDeleted = (id: string) => {
    setResources((prev) => prev.filter((r) => r.id !== id));
  };

  const handleChannelModeChanged = (id: string, mode: string) => {
    setResources((prev) => prev.map((r) => r.id === id ? { ...r, execution_mode: mode } : r));
  };

  // Kênh không thuộc nhóm nào (country không khớp group nào)
  const groupNames = new Set(groups.map((g) => g.name));
  const ungrouped = resources.filter((r) => !r.country || !groupNames.has(r.country));

  return (
    <div>
      <PageHeader
        title="Kênh của tôi"
        description="Gom kênh YouTube, TikTok, Facebook theo từng nhóm quốc gia. Tool dự phòng sẽ tự kéo dữ liệu về."
      />

      {/* Tạo nhóm mới */}
      <Section
        title="Tạo nhóm quốc gia"
        description="Ví dụ: Nigeria (NG), Việt Nam (VN), Philippines (PH)."
      >
        <form
          className="flex max-w-xl flex-wrap items-end gap-3"
          onSubmit={(e) => { e.preventDefault(); void handleCreateGroup(); }}
        >
          <Field label="Tên quốc gia" hint="Dùng để nhóm kênh. Ví dụ: Nigeria.">
            {(p) => (
              <Input
                {...p}
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Nigeria"
                list="country-suggestions"
                className="w-44"
              />
            )}
          </Field>
          <datalist id="country-suggestions">
            {COMMON_COUNTRIES.map(([code, name]) => (
              <option key={code} value={name} />
            ))}
          </datalist>
          <Field label="Mã ISO" optional hint="2 ký tự. Ví dụ: NG">
            {(p) => (
              <Input
                {...p}
                value={newCode}
                onChange={(e) => setNewCode(e.target.value.toUpperCase().slice(0, 10))}
                placeholder="NG"
                maxLength={10}
                className="w-20 font-mono"
              />
            )}
          </Field>
          <Button type="submit" disabled={creating || !newName.trim()}>
            <Plus aria-hidden /> {creating ? "Đang tạo…" : "Tạo nhóm"}
          </Button>
        </form>
      </Section>

      {/* Danh sách nhóm */}
      <Section title={`Nhóm quốc gia (${groups.length})`}>
        {loadError && <Notice tone="error" className="mb-4">{loadError}</Notice>}
        {loading ? (
          <p role="status" className="py-6 text-sm text-muted-foreground">Đang tải…</p>
        ) : groups.length === 0 ? (
          <EmptyState icon={Globe} title="Chưa có nhóm quốc gia nào">
            Tạo nhóm đầu tiên ở trên rồi thêm kênh vào.
          </EmptyState>
        ) : (
          <div className="space-y-3">
            {groups.map((g) => (
              <GroupSection
                key={g.id}
                group={g}
                platforms={platforms}
                resources={resources}
                onRename={handleRename}
                onDelete={handleDeleteGroup}
                onChannelAdded={handleChannelAdded}
                onChannelDeleted={handleChannelDeleted}
                onChannelModeChanged={handleChannelModeChanged}
              />
            ))}
          </div>
        )}
      </Section>

      {/* Kênh chưa phân nhóm */}
      {ungrouped.length > 0 && (
        <Section title={`Kênh chưa phân nhóm (${ungrouped.length})`}
          description="Những kênh này chưa thuộc nhóm quốc gia nào. Xóa hoặc tạo nhóm tương ứng rồi thêm lại.">
          <ul className="divide-y divide-border">
            {ungrouped.map((r) => {
              const label = r.display_name || r.username || r.input_ref;
              return (
                <li key={r.id} className="flex items-center gap-3 py-3">
                  <span className="text-lg">{PLATFORM_EMOJI[r.platform] ?? "🔗"}</span>
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{label}</p>
                    <p className="text-xs text-muted-foreground">
                      {PLATFORM_LABEL[r.platform] ?? r.platform}
                      {r.country ? ` · ${r.country}` : ""}
                    </p>
                  </div>
                  <Button variant="outline" size="sm"
                    onClick={() => void resourcesApi.deleteResource(r.id).then(() => handleChannelDeleted(r.id))}
                    aria-label={`Xóa kênh ${label}`}>
                    <Trash2 aria-hidden className="size-4" /> Xóa
                  </Button>
                </li>
              );
            })}
          </ul>
        </Section>
      )}
    </div>
  );
}
