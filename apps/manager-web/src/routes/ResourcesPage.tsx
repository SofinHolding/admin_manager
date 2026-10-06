/**
 * Trang Nguồn & Resource — viewer không có Tool A tự khai báo Source (namespace nghiệp vụ, vd
 * "Facebook / ITL") và Resource (page/channel/profile cụ thể) ngay trên website.
 *
 * Theo `tasks/backlog/local-operations-hub-v1.md` mục 7: website CHỈ ghi inventory, KHÔNG tự chạy
 * crawler — Admin/ADD đăng nhập Tool B (Local Fallback Tool, phase C) sẽ kéo resource này về chạy.
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Database, FolderPlus, Import, Plus, Trash2 } from "lucide-react";
import { resourcesApi, type ManagedResource, type ResourceSource } from "../api";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Textarea } from "../components/ui/textarea";
import {
  Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger,
} from "../components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const inputCls =
  "h-9 w-full rounded-md border border-border bg-elev-1 px-3 text-sm outline-none transition-colors focus:border-primary";

export default function ResourcesPage() {
  const [sources, setSources] = useState<ResourceSource[]>([]);
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [resources, setResources] = useState<ManagedResource[]>([]);
  const [loadingResources, setLoadingResources] = useState(false);

  const [newSourceName, setNewSourceName] = useState("");
  const [newSourcePlatform, setNewSourcePlatform] = useState("");
  const [newSourceCountry, setNewSourceCountry] = useState("");
  const [creatingSource, setCreatingSource] = useState(false);

  const selectedSource = sources.find((s) => s.id === selectedSourceId) || null;

  const loadSources = async () => {
    try {
      const [{ items }, { items: plats }] = await Promise.all([
        resourcesApi.listSources(), resourcesApi.listPlatforms(),
      ]);
      setSources(items);
      setPlatforms(plats.map((p) => p.platform));
      if (!selectedSourceId && items.length > 0) setSelectedSourceId(items[0].id);
    } catch (e) {
      toast.error(errMsg(e, "Không tải được danh sách nguồn"));
    }
  };

  useEffect(() => { loadSources(); }, []);

  useEffect(() => {
    if (!selectedSourceId) { setResources([]); return; }
    setLoadingResources(true);
    resourcesApi.listResources({ source_instance_id: selectedSourceId })
      .then((d) => setResources(d.items))
      .catch((e) => toast.error(errMsg(e, "Không tải được resource")))
      .finally(() => setLoadingResources(false));
  }, [selectedSourceId]);

  const handleCreateSource = async () => {
    if (!newSourceName.trim() || !newSourcePlatform) return;
    setCreatingSource(true);
    try {
      const s = await resourcesApi.createSource({
        name: newSourceName.trim(), platform: newSourcePlatform,
        default_country: newSourceCountry.trim(),
      });
      setSources((prev) => [s, ...prev]);
      setSelectedSourceId(s.id);
      setNewSourceName(""); setNewSourcePlatform(""); setNewSourceCountry("");
      toast.success("Đã tạo nguồn mới");
    } catch (e) {
      toast.error(errMsg(e, "Tạo nguồn thất bại"));
    } finally {
      setCreatingSource(false);
    }
  };

  const reloadResources = async () => {
    if (!selectedSourceId) return;
    const d = await resourcesApi.listResources({ source_instance_id: selectedSourceId });
    setResources(d.items);
  };

  const handleDeleteResource = async (r: ManagedResource) => {
    if (!confirm(`Xoá resource "${r.display_name || r.input_ref}"?`)) return;
    try {
      await resourcesApi.deleteResource(r.id);
      setResources((prev) => prev.filter((x) => x.id !== r.id));
      toast.success("Đã xoá resource");
    } catch (e) {
      toast.error(errMsg(e, "Xoá resource thất bại"));
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <h1 className="flex items-center gap-2 text-xl font-bold">
        <Database className="size-5" /> Nguồn &amp; Resource
      </h1>
      <p className="-mt-4 text-sm text-muted-foreground">
        Không có Tool A? Khai báo nguồn (Source) và nhập trang/kênh/tài khoản (Resource) ở đây —
        Admin sẽ dùng Tool B để chạy thay bạn với các nền tảng được cho phép.
      </p>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
        {/* ── Cột trái: Source ── */}
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-elev-1 p-4">
            <h3 className="mb-3 flex items-center gap-1.5 text-sm font-semibold">
              <FolderPlus className="size-4" /> Tạo nguồn mới
            </h3>
            {platforms.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                Chưa có nền tảng nào được Admin cho phép nhập từ website.
              </p>
            ) : (
              <div className="space-y-2">
                <input type="text" className={inputCls} placeholder="Tên nguồn, vd: Facebook ITL"
                       value={newSourceName} onChange={(e) => setNewSourceName(e.target.value)}
                       maxLength={200} />
                <Select value={newSourcePlatform} onValueChange={setNewSourcePlatform}>
                  <SelectTrigger className="h-9"><SelectValue placeholder="Chọn nền tảng" /></SelectTrigger>
                  <SelectContent>
                    {platforms.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
                  </SelectContent>
                </Select>
                <input type="text" className={inputCls} placeholder="Quốc gia mặc định (tuỳ chọn)"
                       value={newSourceCountry} onChange={(e) => setNewSourceCountry(e.target.value)}
                       maxLength={100} />
                <Button onClick={handleCreateSource} className="h-9 w-full gap-1.5"
                        disabled={creatingSource || !newSourceName.trim() || !newSourcePlatform}>
                  <Plus className="size-4" /> Tạo nguồn
                </Button>
              </div>
            )}
          </div>

          <div className="space-y-2">
            {sources.length === 0 ? (
              <p className="px-1 text-sm text-muted-foreground">Chưa có nguồn nào.</p>
            ) : sources.map((s) => (
              <button
                key={s.id}
                onClick={() => setSelectedSourceId(s.id)}
                className={`w-full rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                  s.id === selectedSourceId
                    ? "border-primary bg-primary/10 font-medium"
                    : "border-border hover:bg-elev-2"}`}
              >
                <div>{s.name}</div>
                <div className="text-xs text-muted-foreground">
                  {s.platform}{s.default_country ? ` · ${s.default_country}` : ""}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* ── Cột phải: Resource của nguồn đang chọn ── */}
        <div className="rounded-xl border border-border bg-elev-1 p-4">
          {!selectedSource ? (
            <p className="text-sm text-muted-foreground">Chọn hoặc tạo một nguồn ở cột bên trái.</p>
          ) : (
            <ResourcePanel
              source={selectedSource}
              resources={resources}
              loading={loadingResources}
              onCreated={reloadResources}
              onDelete={handleDeleteResource}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function ResourcePanel({ source, resources, loading, onCreated, onDelete }: {
  source: ResourceSource;
  resources: ManagedResource[];
  loading: boolean;
  onCreated: () => void;
  onDelete: (r: ManagedResource) => void;
}) {
  const [inputRef, setInputRef] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [country, setCountry] = useState(source.default_country);
  const [adding, setAdding] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importLines, setImportLines] = useState("");
  const [importing, setImporting] = useState(false);

  const handleAdd = async () => {
    if (!inputRef.trim()) return;
    setAdding(true);
    try {
      await resourcesApi.createResource({
        source_instance_id: source.id, platform: source.platform,
        input_ref: inputRef.trim(), display_name: displayName.trim(), country: country.trim(),
      });
      setInputRef(""); setDisplayName("");
      await onCreated();
      toast.success("Đã thêm resource");
    } catch (e) {
      toast.error(errMsg(e, "Thêm resource thất bại"));
    } finally {
      setAdding(false);
    }
  };

  const handleImport = async () => {
    const lines = importLines.split("\n").map((l) => l.trim()).filter(Boolean);
    if (lines.length === 0) return;
    setImporting(true);
    try {
      const d = await resourcesApi.importResources({
        source_instance_id: source.id, platform: source.platform, lines,
      });
      await onCreated();
      setImportLines(""); setImportOpen(false);
      toast.success(`Đã nhập ${d.created_count} resource` +
        (d.skipped_count > 0 ? `, bỏ qua ${d.skipped_count} dòng trùng` : ""));
    } catch (e) {
      toast.error(errMsg(e, "Nhập hàng loạt thất bại"));
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-semibold">{source.name}</h3>
          <p className="text-xs text-muted-foreground">
            {source.platform}{source.project_key ? ` · ${source.project_key}` : ""}
          </p>
        </div>
        <Dialog open={importOpen} onOpenChange={setImportOpen}>
          <DialogTrigger asChild>
            <Button size="sm" variant="outline" className="gap-1.5">
              <Import className="size-4" /> Nhập hàng loạt
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogTitle>Nhập resource hàng loạt — {source.name}</DialogTitle>
            <DialogDescription>
              Mỗi dòng 1 URL, username, hoặc ID kênh/trang. Tối đa 500 dòng mỗi lần.
            </DialogDescription>
            <Textarea className="mt-3 min-h-[200px]" value={importLines}
                      onChange={(e) => setImportLines(e.target.value)}
                      placeholder={"https://facebook.com/page1\n@username2\nchannel_id_3"} />
            <Button className="mt-3 w-full" onClick={handleImport}
                    disabled={importing || !importLines.trim()}>
              {importing ? "Đang nhập…" : "Nhập resource"}
            </Button>
          </DialogContent>
        </Dialog>
      </div>

      <div className="flex flex-wrap items-end gap-2 border-y border-border py-3">
        <div className="min-w-[200px] flex-1">
          <label className="mb-1 block text-xs text-muted-foreground">URL / username / ID</label>
          <input type="text" className={inputCls} value={inputRef}
                 onChange={(e) => setInputRef(e.target.value)} maxLength={500}
                 placeholder="https://facebook.com/trang-cua-ban" />
        </div>
        <div className="w-48">
          <label className="mb-1 block text-xs text-muted-foreground">Tên hiển thị (tuỳ chọn)</label>
          <input type="text" className={inputCls} value={displayName}
                 onChange={(e) => setDisplayName(e.target.value)} maxLength={200} />
        </div>
        <div className="w-36">
          <label className="mb-1 block text-xs text-muted-foreground">Quốc gia</label>
          <input type="text" className={inputCls} value={country}
                 onChange={(e) => setCountry(e.target.value)} maxLength={100} />
        </div>
        <Button size="sm" className="h-9 gap-1.5" onClick={handleAdd}
                disabled={adding || !inputRef.trim()}>
          <Plus className="size-4" /> Thêm
        </Button>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">Đang tải…</p>
      ) : resources.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nguồn này chưa có resource nào.</p>
      ) : (
        <div className="space-y-2">
          {resources.map((r) => (
            <div key={r.id} className="flex items-center justify-between gap-2 rounded-md border border-border/60 px-3 py-2 text-sm">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{r.display_name || r.input_ref}</div>
                <div className="truncate text-xs text-muted-foreground">{r.input_ref}</div>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                {r.mapping_status === "mapped"
                  ? <Badge variant="success">Đã map</Badge>
                  : <Badge variant="warning">Chưa map</Badge>}
                {r.execution_mode === "fallback_local"
                  ? <Badge variant="success">Fallback Local</Badge>
                  : r.execution_mode === "primary_tool"
                  ? <Badge>Primary Tool</Badge>
                  : <Badge variant="outline">Tắt</Badge>}
                <Button variant="ghost" size="sm" onClick={() => onDelete(r)}
                        className="text-red-500 hover:text-red-600" title="Xoá resource">
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
