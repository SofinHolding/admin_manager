/**
 * Kênh của tôi (v2) — viewer tự quản lý nhóm quốc gia và kênh trong từng nhóm.
 *
 * Layout:
 *  - Tạo nhóm quốc gia mới (form inline)
 *  - Accordion per group: tên nước (người dùng tự gõ, không có danh sách dựng sẵn) + danh sách kênh + form thêm kênh
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
import { platformEmoji, platformLabel, type PlatformNameMap } from "../lib/platforms";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";
import { Badge } from "../components/ui/badge";
import { EmptyState, Field, Notice, PageHeader, Section } from "../components/kit";

// ── Hằng số ──────────────────────────────────────────────────────────────────

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

// ── AddChannelsForm ────────────────────────────────────────────────────────────

const NEW_COUNTRY = "__new__";
const MAX_LINKS = 100;      // mỗi link = một lần tạo kênh (POST /v1/resources) nên giới hạn một lượt cho hợp lý
const CONCURRENCY = 5;

function AddChannelsForm({
  groups, platforms, names, existing, onGroupCreated, onAdded,
}: {
  groups: ChannelGroup[];
  platforms: string[];
  names: PlatformNameMap;
  existing: ManagedResource[];
  onGroupCreated: (g: ChannelGroup) => void;
  onAdded: (list: ManagedResource[]) => void;
}) {
  const [platform, setPlatform] = useState("");
  const [countryId, setCountryId] = useState("");
  const [newName, setNewName] = useState("");
  const [links, setLinks] = useState("");
  const [saving, setSaving] = useState(false);

  const creatingCountry = countryId === NEW_COUNTRY;
  // Mỗi dòng một link; bỏ dòng trống và dòng trùng (không phân biệt hoa/thường) — số hiển thị = số kênh sẽ tạo
  const lines = [...new Map(links.split(/\r?\n/).map((l) => l.trim()).filter(Boolean).map((l) => [l.toLowerCase(), l])).values()];
  const countryOk = creatingCountry ? !!newName.trim() : !!countryId;
  const canSubmit = !saving && !!platform && countryOk && lines.length > 0 && lines.length <= MAX_LINKS;

  const handleAdd = async () => {
    if (!canSubmit) return;
    setSaving(true);
    try {
      // 1) Quốc gia: có sẵn, hoặc tạo mới ngay trong form (kênh luôn gắn đúng quốc gia đã chọn)
      let group = groups.find((g) => g.id === countryId);
      if (creatingCountry) {
        const name = newName.trim();
        group = groups.find((g) => g.name.toLowerCase() === name.toLowerCase())
          ?? await channelGroupsApi.create(name);
        onGroupCreated(group);
      }
      if (!group) return;
      const country = group.name;

      // 2) Nguồn (Source) theo nền tảng + quốc gia: tìm sẵn hoặc tạo — giữ nguyên cách lưu cũ
      const sources = await resourcesApi.listSources();
      const source = sources.items.find((s) => s.platform === platform && s.default_country === country)
        ?? await resourcesApi.createSource({
          name: `${platformLabel(platform, names)} · ${country}`, platform, default_country: country,
        });

      // 3) Mỗi dòng = một kênh: gắn nền tảng đã chọn + quốc gia để map đúng nhóm. Link trùng / đã có thì bỏ qua và báo.
      const knownRefs = new Set(existing.map((r) => r.input_ref.trim().toLowerCase()));
      const knownIds = new Set(existing.map((r) => r.id));
      const unique: string[] = [];
      const skipped: string[] = [];
      for (const line of lines) {
        const key = line.toLowerCase();
        if (knownRefs.has(key)) { skipped.push(line); continue; }
        knownRefs.add(key);
        unique.push(line);
      }
      const created: ManagedResource[] = [];
      const failed: string[] = [];
      for (let i = 0; i < unique.length; i += CONCURRENCY) {
        await Promise.all(unique.slice(i, i + CONCURRENCY).map(async (line) => {
          try {
            const r = await resourcesApi.createResource({
              source_instance_id: source.id, platform, input_ref: line, country,
            });
            if (knownIds.has(r.id)) skipped.push(line);   // server nhận ra kênh đã có (cùng danh tính) => không nhân đôi
            else { knownIds.add(r.id); created.push(r); }
          } catch {
            failed.push(line);
          }
        }));
      }
      if (created.length) onAdded(created);
      setLinks(failed.join("\n"));          // chỉ giữ lại các dòng lỗi để thử lại
      setCountryId(group.id);
      setNewName("");
      const extra = [
        skipped.length ? `bỏ qua ${skipped.length} link đã có/trùng` : "",
        failed.length ? `${failed.length} link lỗi (giữ lại trong ô để thử lại)` : "",
      ].filter(Boolean).join("; ");
      if (created.length) toast.success(`Đã thêm ${created.length} kênh vào ${country}${extra ? " — " + extra : ""}.`);
      else toast.warning(`Không thêm kênh nào${extra ? " — " + extra : ""}.`);
    } catch (e) {
      toast.error(errorMessage(e, "Không thêm được kênh. Hãy thử lại."));
    } finally {
      setSaving(false);
    }
  };

  if (platforms.length === 0) {
    return (
      <Notice tone="info">
        Chưa có nền tảng nào được bật. Quản trị viên cần tạo/bật nền tảng ở trang "Nền tảng" trước khi thêm kênh.
      </Notice>
    );
  }

  return (
    <form
      className="grid max-w-3xl gap-4"
      onSubmit={(e) => { e.preventDefault(); void handleAdd(); }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Nền tảng" hint="Bắt buộc — dashboard dùng đúng nền tảng này để tính số liệu.">
          {(p) => (
            <Select value={platform} onValueChange={setPlatform}>
              <SelectTrigger {...p}><SelectValue placeholder="Chọn nền tảng" /></SelectTrigger>
              <SelectContent>
                {platforms.map((pl) => (
                  <SelectItem key={pl} value={pl}>
                    {platformEmoji(pl)} {platformLabel(pl, names)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label="Quốc gia" hint="Chọn quốc gia của bạn hoặc tạo mới ngay tại đây.">
          {(p) => (
            <Select value={countryId} onValueChange={setCountryId}>
              <SelectTrigger {...p}><SelectValue placeholder="Chọn quốc gia" /></SelectTrigger>
              <SelectContent>
                {groups.map((g) => (
                  <SelectItem key={g.id} value={g.id}>
                    {g.name}{g.country_code ? ` (${g.country_code})` : ""}
                  </SelectItem>
                ))}
                <SelectItem value={NEW_COUNTRY}>+ Tạo quốc gia mới…</SelectItem>
              </SelectContent>
            </Select>
          )}
        </Field>
      </div>

      {creatingCountry && (
        <div className="rounded-lg border border-dashed border-border p-4">
          <Field label="Tên quốc gia mới" hint="Gõ tên theo cách bạn muốn. Quản trị viên sẽ chuẩn hóa cách viết sau.">
            {(p) => (
              <Input
                {...p}
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Nigeria"
                maxLength={100}
              />
            )}
          </Field>
        </div>
      )}

      <Field
        label="Link kênh"
        hint={`Mỗi dòng một link hoặc @username (tối đa ${MAX_LINKS}). Đang có ${lines.length} link.`}
      >
        {(p) => (
          <textarea
            {...p}
            value={links}
            onChange={(e) => setLinks(e.target.value)}
            rows={6}
            placeholder={"https://www.tiktok.com/@kenh1\nhttps://www.tiktok.com/@kenh2"}
            className="w-full rounded-md border border-border bg-transparent px-3 py-2 font-mono text-sm"
          />
        )}
      </Field>

      <div>
        <Button type="submit" disabled={!canSubmit}>
          <Plus aria-hidden /> {saving ? "Đang thêm…" : `Thêm ${lines.length > 1 ? `${lines.length} kênh` : "kênh"}`}
        </Button>
      </div>
    </form>
  );
}

// ── GroupSection (accordion item) ─────────────────────────────────────────────

function GroupSection({
  group, names, resources,
  onRename, onDelete, onChannelDeleted, onChannelModeChanged,
}: {
  group: ChannelGroup;
  names: PlatformNameMap;
  resources: ManagedResource[];
  onRename: (id: string, name: string, code: string) => void;
  onDelete: (id: string) => void;
  onChannelDeleted: (id: string) => void;
  onChannelModeChanged: (id: string, mode: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(group.name);
  const [renaming, setRenaming] = useState(false);
  const [deletingChannelId, setDeletingChannelId] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const channels = resources.filter((r) => r.country === group.name);

  const handleRename = async () => {
    if (!editName.trim() || editName.trim() === group.name) {
      setEditing(false);
      return;
    }
    setRenaming(true);
    try {
      await channelGroupsApi.update(group.id, { name: editName.trim() });
      onRename(group.id, editName.trim(), group.country_code);
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
            <Button variant="outline" size="sm" onClick={() => { setEditing(false); setEditName(group.name); }}>
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
                    <span className="text-lg" aria-label={platformLabel(r.platform, names)}>
                      {platformEmoji(r.platform)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{label}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {platformLabel(r.platform, names)}
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
        </div>
      )}
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function ChannelsPage() {
  const [groups, setGroups] = useState<ChannelGroup[]>([]);
  const [resources, setResources] = useState<ManagedResource[]>([]);
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [platformNames, setPlatformNames] = useState<PlatformNameMap>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Tạo nhóm mới
  const [newName, setNewName] = useState("");
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
      const nameMap: PlatformNameMap = {};
      for (const p of plat.items) nameMap[p.platform] = p.display_name || p.platform;
      setPlatformNames(nameMap);
      setPlatforms(plat.items.map((p) => p.platform));
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
      const group = await channelGroupsApi.create(newName.trim());
      setGroups((prev) => [...prev, group]);
      setNewName("");
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

  const handleGroupCreated = (group: ChannelGroup) => {
    setGroups((prev) => (prev.some((g) => g.id === group.id) ? prev : [...prev, group]));
  };

  const handleChannelsAdded = (list: ManagedResource[]) => {
    setResources((prev) => [...list, ...prev]);
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
        description="Gom kênh theo từng nhóm quốc gia. Tool dự phòng sẽ tự kéo dữ liệu về."
      />

      {/* Tạo nhóm mới */}
      <Section
        title="Tạo nhóm quốc gia"
        description="Gõ tên quốc gia theo cách bạn muốn, ví dụ: Nigeria, Việt Nam. Quản trị viên sẽ chuẩn hóa cách viết sau."
      >
        <form
          className="flex max-w-xl flex-wrap items-end gap-3"
          onSubmit={(e) => { e.preventDefault(); void handleCreateGroup(); }}
        >
          <Field label="Tên quốc gia" hint="Dùng để nhóm kênh. Tự do nhập, không cần chọn từ danh sách.">
            {(p) => (
              <Input
                {...p}
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Nigeria"
                maxLength={100}
                className="w-64"
              />
            )}
          </Field>
          <Button type="submit" disabled={creating || !newName.trim()}>
            <Plus aria-hidden /> {creating ? "Đang tạo…" : "Tạo nhóm"}
          </Button>
        </form>
      </Section>

      {/* Thêm kênh: chọn Nền tảng + Quốc gia rồi dán nhiều link (mỗi dòng một link) */}
      <Section
        title="Thêm kênh"
        description="Chọn nền tảng và quốc gia, rồi dán link kênh — mỗi dòng một link, thêm được nhiều kênh cùng lúc."
      >
        <AddChannelsForm
          groups={groups}
          platforms={platforms}
          names={platformNames}
          existing={resources}
          onGroupCreated={handleGroupCreated}
          onAdded={handleChannelsAdded}
        />
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
                names={platformNames}
                resources={resources}
                onRename={handleRename}
                onDelete={handleDeleteGroup}
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
                  <span className="text-lg">{platformEmoji(r.platform)}</span>
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{label}</p>
                    <p className="text-xs text-muted-foreground">
                      {platformLabel(r.platform, platformNames)}
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
