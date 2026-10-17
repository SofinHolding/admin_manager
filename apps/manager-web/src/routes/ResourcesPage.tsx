/**
 * Kênh và nguồn — người xem không có công cụ tự đẩy dữ liệu khai báo NGUỒN (namespace nghiệp vụ, vd
 * "Facebook / ITL") và KÊNH (trang/kênh/tài khoản cụ thể) ngay trên website.
 *
 * Từ ngữ giao diện: Source = "nguồn", Resource = "kênh". Bố cục: MỘT cột đọc, điều hướng hai bước
 * (danh sách nguồn -> chi tiết kênh của nguồn) trên mọi cỡ màn hình, không ép hai cột.
 *
 * Theo `tasks/backlog/local-operations-hub-v1.md` mục 7: website CHỈ ghi inventory, KHÔNG tự chạy
 * crawler — quản trị viên đăng nhập công cụ dự phòng sẽ kéo kênh này về chạy.
 */

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, CheckCircle2, ChevronRight, CircleDashed, Database, Import, Plus, Trash2 } from "lucide-react";
import { resourcesApi, type ImportSkip, type ManagedResource, type ResourceSource } from "../api";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import {
  Dialog, DialogContent, DialogDescription, DialogTitle,
} from "../components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";
import { EmptyState, Field, Notice, PageHeader, Section, TechDetails } from "../components/kit";
import { cn } from "../lib/utils";
import { platformLabel, type PlatformNameMap } from "../lib/platforms";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const MAX_IMPORT = 500;

/** Tách các dòng dán vào: bỏ dòng trống, gộp dòng lặp, đối chiếu với kênh đã có. Thuần để dễ test. */
function previewImport(text: string, existingRefs: Iterable<string>) {
  const have = new Set(Array.from(existingRefs, (r) => r.trim().toLowerCase()));
  const seen = new Set<string>();
  const toAdd: string[] = [];
  let duplicates = 0;
  let existing = 0;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const key = line.toLowerCase();
    if (have.has(key)) { existing += 1; continue; }
    if (seen.has(key)) { duplicates += 1; continue; }
    seen.add(key);
    toAdd.push(line);
  }
  return { toAdd, skipped: existing + duplicates, existing, duplicates, tooMany: toAdd.length > MAX_IMPORT };
}

export default function ResourcesPage() {
  const [sources, setSources] = useState<ResourceSource[]>([]);
  const [loadingSources, setLoadingSources] = useState(true);
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [platformNames, setPlatformNames] = useState<PlatformNameMap>({});
  /** Bước 1 = danh sách nguồn (rỗng), bước 2 = chi tiết kênh của nguồn đã chọn. */
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
      const nameMap: PlatformNameMap = {};
      for (const p of plats) nameMap[p.platform] = p.display_name || p.platform;
      setPlatformNames(nameMap);
      setPlatforms(plats.map((p) => p.platform));
    } catch (e) {
      toast.error(errMsg(e, "Không tải được danh sách nguồn. Kiểm tra mạng rồi tải lại trang."));
    } finally {
      setLoadingSources(false);
    }
  };

  useEffect(() => { loadSources(); }, []);

  useEffect(() => {
    if (!selectedSourceId) { setResources([]); return; }
    setLoadingResources(true);
    resourcesApi.listResources({ source_instance_id: selectedSourceId })
      .then((d) => setResources(d.items))
      .catch((e) => toast.error(errMsg(e, "Không tải được danh sách kênh")))
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
      toast.success("Đã tạo nguồn");
    } catch (e) {
      toast.error(errMsg(e, "Không tạo được nguồn. Kiểm tra thông tin rồi thử lại."));
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
    if (!confirm(`Xóa kênh "${r.display_name || r.input_ref}" khỏi nguồn này? Kênh sẽ biến mất khỏi danh sách; bạn có thể thêm lại sau.`)) return;
    try {
      await resourcesApi.deleteResource(r.id);
      setResources((prev) => prev.filter((x) => x.id !== r.id));
      toast.success("Đã xóa kênh");
    } catch (e) {
      toast.error(errMsg(e, "Không xóa được kênh. Thử lại sau ít phút."));
    }
  };

  /* ── Bước 2: chi tiết kênh của một nguồn ── */
  if (selectedSource) {
    return (
      <div>
        <Button variant="ghost" className="-ml-3 mb-2" onClick={() => setSelectedSourceId("")}>
          <ArrowLeft aria-hidden /> Quay lại danh sách nguồn
        </Button>
        <ResourcePanel
          source={selectedSource}
          names={platformNames}
          resources={resources}
          loading={loadingResources}
          onCreated={reloadResources}
          onDelete={handleDeleteResource}
        />
      </div>
    );
  }

  /* ── Bước 1: danh sách nguồn + tạo nguồn ── */
  return (
    <div>
      <PageHeader
        title="Kênh và nguồn"
        description="Nguồn là nơi công cụ lấy dữ liệu; kênh là trang hoặc kênh cụ thể (ví dụ một trang Facebook). Nếu bạn chưa có công cụ tự đẩy dữ liệu, hãy khai báo nguồn và kênh ở đây, quản trị viên sẽ chạy thay bạn."
      />

      <Section title="Nguồn của bạn" description={sources.length ? "Chọn một nguồn để xem và thêm kênh." : undefined}>
        {loadingSources ? (
          <p className="text-muted-foreground">Đang tải…</p>
        ) : sources.length === 0 ? (
          <EmptyState icon={Database} title="Bạn chưa có nguồn nào">
            Hãy tạo nguồn đầu tiên bằng biểu mẫu bên dưới: đặt tên, chọn nền tảng. Sau đó bạn thêm các kênh vào nguồn đó.
          </EmptyState>
        ) : (
          <ul className="divide-y divide-border border-y border-border">
            {sources.map((s) => (
              <li key={s.id}>
                <button type="button" onClick={() => setSelectedSourceId(s.id)}
                        className="flex min-h-14 w-full items-center gap-3 px-1 py-2 text-left hover:bg-elev-2">
                  <span className="min-w-0 flex-1">
                    <span className="block break-words font-semibold">{s.name}</span>
                    <span className="block text-sm text-muted-foreground">
                      {platformLabel(s.platform, platformNames)}{s.default_country ? ` · Quốc gia mặc định: ${s.default_country}` : ""}
                    </span>
                  </span>
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Tạo nguồn mới" description="Mỗi nguồn gắn với một nền tảng, ví dụ “Facebook của công ty”.">
        {platforms.length === 0 ? (
          <Notice tone="info" title="Chưa có nền tảng nào được mở cho bạn">
            Quản trị viên cần cho phép nhập từ website trước. Hãy nhờ quản trị viên bật nền tảng bạn dùng.
          </Notice>
        ) : (
          <form className="max-w-xl space-y-4" noValidate
                onSubmit={(e) => { e.preventDefault(); void handleCreateSource(); }}>
            <Field label="Tên nguồn" hint="Đặt tên dễ nhớ, ví dụ “Facebook ITL”.">
              {(p) => <Input {...p} value={newSourceName} maxLength={200} onChange={(e) => setNewSourceName(e.target.value)} />}
            </Field>
            <Field label="Nền tảng">
              {(p) => (
                <Select value={newSourcePlatform} onValueChange={setNewSourcePlatform}>
                  <SelectTrigger id={p.id} aria-describedby={p["aria-describedby"]}><SelectValue placeholder="Chọn nền tảng" /></SelectTrigger>
                  <SelectContent>
                    {platforms.map((pl) => <SelectItem key={pl} value={pl}>{platformLabel(pl, platformNames)}</SelectItem>)}
                  </SelectContent>
                </Select>
              )}
            </Field>
            <Field label="Quốc gia mặc định" optional hint="Dùng cho kênh bạn thêm vào nguồn này nếu không ghi riêng quốc gia.">
              {(p) => <Input {...p} value={newSourceCountry} maxLength={100} onChange={(e) => setNewSourceCountry(e.target.value)} />}
            </Field>
            <Button type="submit" disabled={creatingSource || !newSourceName.trim() || !newSourcePlatform}>
              <Plus aria-hidden /> Tạo nguồn
            </Button>
          </form>
        )}
      </Section>
    </div>
  );
}

function MappingBadge({ r }: { r: ManagedResource }) {
  const mapped = r.mapping_status === "mapped";
  const Icon = mapped ? CheckCircle2 : CircleDashed;
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-0.5 text-[0.8125rem] font-medium",
      mapped ? "bg-success/12 text-success" : "bg-warning/15 text-warning")}>
      <Icon className="size-3.5" aria-hidden />{mapped ? "Đã liên kết số liệu" : "Chưa liên kết số liệu"}
    </span>
  );
}

const modeText = (m: string) =>
  m === "fallback_local" ? "Chạy bằng công cụ dự phòng" : m === "primary_tool" ? "Chạy bằng công cụ chính" : "Chưa được chạy";

type ImportPhase = "edit" | "preview" | "done";

function ResourcePanel({ source, names, resources, loading, onCreated, onDelete }: {
  source: ResourceSource;
  names: PlatformNameMap;
  resources: ManagedResource[];
  loading: boolean;
  onCreated: () => void | Promise<void>;
  onDelete: (r: ManagedResource) => void;
}) {
  const [inputRef, setInputRef] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [country, setCountry] = useState(source.default_country);
  const [adding, setAdding] = useState(false);

  const [importOpen, setImportOpen] = useState(false);
  const [phase, setPhase] = useState<ImportPhase>("edit");
  const [importLines, setImportLines] = useState("");
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<{ created: number; skipped: number; skips: ImportSkip[] } | null>(null);

  const existingRefs = useMemo(() => resources.map((r) => r.input_ref), [resources]);
  const preview = useMemo(() => previewImport(importLines, existingRefs), [importLines, existingRefs]);

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
      toast.success("Đã thêm kênh");
    } catch (e) {
      toast.error(errMsg(e, "Không thêm được kênh. Kiểm tra địa chỉ rồi thử lại."));
    } finally {
      setAdding(false);
    }
  };

  const resetImport = () => { setPhase("edit"); setImportLines(""); setResult(null); };

  const handleImport = async () => {
    if (preview.toAdd.length === 0 || preview.tooMany) return;
    setImporting(true);
    try {
      const d = await resourcesApi.importResources({
        source_instance_id: source.id, platform: source.platform, lines: preview.toAdd,
      });
      await onCreated();
      const skipped = d.skipped_count + preview.skipped;
      setResult({ created: d.created_count, skipped, skips: d.skipped ?? [] });
      setPhase("done");
      toast.success(`Đã thêm ${d.created_count} kênh` + (skipped > 0 ? `, bỏ qua ${skipped} kênh đã có` : ""));
    } catch (e) {
      toast.error(errMsg(e, "Không nhập được hàng loạt. Kiểm tra danh sách rồi thử lại."));
    } finally {
      setImporting(false);
    }
  };

  return (
    <div>
      <PageHeader
        title={source.name}
        description={`${platformLabel(source.platform, names)}${source.default_country ? ` · Quốc gia mặc định: ${source.default_country}` : ""}`}
        actions={
          <Button variant="outline" onClick={() => { resetImport(); setImportOpen(true); }}>
            <Import aria-hidden /> Nhập nhiều kênh
          </Button>
        }
      />

      <Section title="Thêm một kênh" description="Dán địa chỉ trang, tên người dùng hoặc mã kênh.">
        <form className="max-w-xl space-y-4" noValidate onSubmit={(e) => { e.preventDefault(); void handleAdd(); }}>
          <Field label="Địa chỉ, tên người dùng hoặc mã kênh">
            {(p) => <Input {...p} value={inputRef} maxLength={500} onChange={(e) => setInputRef(e.target.value)} placeholder="https://facebook.com/trang-cua-ban" />}
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Tên hiển thị" optional>
              {(p) => <Input {...p} value={displayName} maxLength={200} onChange={(e) => setDisplayName(e.target.value)} />}
            </Field>
            <Field label="Quốc gia" optional>
              {(p) => <Input {...p} value={country} maxLength={100} onChange={(e) => setCountry(e.target.value)} />}
            </Field>
          </div>
          <Button type="submit" disabled={adding || !inputRef.trim()}><Plus aria-hidden /> Thêm kênh</Button>
        </form>
      </Section>

      <Section title={`Kênh trong nguồn này${resources.length ? ` (${resources.length})` : ""}`}>
        {loading ? (
          <p className="text-muted-foreground">Đang tải…</p>
        ) : resources.length === 0 ? (
          <EmptyState icon={Database} title="Nguồn này chưa có kênh nào">
            Thêm kênh đầu tiên bằng biểu mẫu ở trên, hoặc bấm “Nhập nhiều kênh” để dán cả danh sách một lần.
          </EmptyState>
        ) : (
          <ul className="divide-y divide-border border-y border-border">
            {resources.map((r) => (
              <li key={r.id} className="flex items-start gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <p className="break-words font-semibold">{r.display_name || r.input_ref}</p>
                  {r.display_name && <p className="break-all text-sm text-muted-foreground">{r.input_ref}</p>}
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
                    <MappingBadge r={r} />
                    <span className="text-sm text-muted-foreground">{modeText(r.execution_mode)}</span>
                  </div>
                </div>
                <Button variant="ghost" onClick={() => onDelete(r)} className="shrink-0 text-destructive"
                        aria-label={`Xóa kênh ${r.display_name || r.input_ref}`}>
                  <Trash2 aria-hidden /> <span className="hidden sm:inline">Xóa</span>
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent className="max-w-xl">
          <DialogTitle>Nhập nhiều kênh vào “{source.name}”</DialogTitle>

          {phase === "edit" && (
            <>
              <DialogDescription className="reading mt-1">
                Mỗi dòng một kênh: địa chỉ trang, tên người dùng hoặc mã kênh. Tối đa {MAX_IMPORT} dòng mỗi lần. Bạn sẽ xem trước trước khi nhập.
              </DialogDescription>
              <Textarea className="mt-3 min-h-[200px]" aria-label="Danh sách kênh, mỗi dòng một kênh" value={importLines}
                        onChange={(e) => setImportLines(e.target.value)}
                        placeholder={"https://facebook.com/page1\n@username2\nchannel_id_3"} />
              <div className="mt-4 flex justify-end">
                <Button onClick={() => setPhase("preview")} disabled={!importLines.trim()}>Xem trước</Button>
              </div>
            </>
          )}

          {phase === "preview" && (
            <>
              <DialogDescription className="reading mt-1">Kiểm tra lại trước khi nhập.</DialogDescription>
              <div className="mt-3 space-y-2" data-testid="import-preview">
                <p className="text-lg font-semibold">
                  Sẽ thêm {preview.toAdd.length} kênh{preview.skipped > 0 ? `, bỏ qua ${preview.skipped} kênh đã có hoặc bị lặp` : ""}
                </p>
                {preview.tooMany && (
                  <Notice tone="error" title={`Quá ${MAX_IMPORT} kênh trong một lần`}>
                    Hãy chia danh sách thành nhiều lần nhập, mỗi lần tối đa {MAX_IMPORT} dòng.
                  </Notice>
                )}
                {preview.toAdd.length > 0 && (
                  <ul className="max-h-48 overflow-y-auto border-y border-border text-sm">
                    {preview.toAdd.slice(0, 50).map((l) => <li key={l} className="break-all border-b border-border py-1.5 last:border-b-0">{l}</li>)}
                    {preview.toAdd.length > 50 && <li className="py-1.5 text-muted-foreground">… và {preview.toAdd.length - 50} kênh nữa</li>}
                  </ul>
                )}
                {preview.toAdd.length === 0 && (
                  <Notice tone="warning" title="Không có kênh mới để thêm">Mọi dòng bạn dán đều đã có trong nguồn này hoặc bị lặp.</Notice>
                )}
              </div>
              <div className="mt-4 flex flex-wrap justify-end gap-2">
                <Button variant="outline" onClick={() => setPhase("edit")}>Sửa danh sách</Button>
                <Button onClick={handleImport} disabled={importing || preview.toAdd.length === 0 || preview.tooMany}>
                  {importing ? "Đang nhập…" : `Nhập ${preview.toAdd.length} kênh`}
                </Button>
              </div>
            </>
          )}

          {phase === "done" && result && (
            <>
              <div className="mt-3" data-testid="import-result">
                <Notice tone="success" title={`Đã thêm ${result.created} kênh${result.skipped > 0 ? `, bỏ qua ${result.skipped} kênh đã có` : ""}`}>
                  Các kênh mới đã nằm trong danh sách của nguồn này.
                </Notice>
                {result.skips.length > 0 && (
                  <TechDetails summary="Chi tiết các dòng bị bỏ qua" className="mt-3">
                    <ul className="space-y-1">
                      {result.skips.map((s) => <li key={s.input_ref} className="break-all">{s.input_ref}: {s.reason}</li>)}
                    </ul>
                  </TechDetails>
                )}
              </div>
              <div className="mt-4 flex justify-end">
                <Button onClick={() => setImportOpen(false)}>Xong</Button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
