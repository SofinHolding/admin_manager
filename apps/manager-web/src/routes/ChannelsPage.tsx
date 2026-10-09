/**
 * Kênh của tôi — người có `capability:manual_channel_mgmt` nhưng KHÔNG có vai trò vào khu quản trị
 * (vd viewer không dashboard) tự khai báo kênh/trang mình quản lý. Dùng lại `/v1/resources` (Local
 * Fallback Tool v1) như `/admin/resources`, nhưng tối giản — không có khái niệm "nguồn" riêng, không
 * import hàng loạt, không chỉnh trạng thái mapping (docs/architecture/capability-model-v1.md §8).
 * Server tự ép `owner_account_id` về chính người gọi (không phải admin/dash_admin).
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Database, Plus, Trash2 } from "lucide-react";
import { resourcesApi, type ManagedResource } from "../api";
import { errorMessage } from "../lib/errors";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { EmptyState, Field, Notice, PageHeader, Section } from "../components/kit";

const PLATFORM_LABEL: Record<string, string> = { facebook: "Facebook", youtube: "YouTube" };

export default function ChannelsPage() {
  const [items, setItems] = useState<ManagedResource[]>([]);
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [platform, setPlatform] = useState("");
  const [inputValue, setInputValue] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [res, plat] = await Promise.all([resourcesApi.listResources(), resourcesApi.listPlatforms()]);
      setItems(res.items);
      const names = plat.items.map((p) => p.platform);
      setPlatforms(names);
      setPlatform((cur) => cur || names[0] || "");
      setLoadError(null);
    } catch (e) {
      setLoadError(errorMessage(e, "Không tải được danh sách kênh. Hãy thử lại."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const handleAdd = async () => {
    if (!platform || !inputValue.trim()) return;
    setSaving(true);
    try {
      // Mỗi nền tảng dùng chung đúng một "nguồn" tối giản của tài khoản — không có khái niệm nguồn
      // riêng như /admin/resources; tạo nguồn mặc định nếu chưa có.
      const sources = await resourcesApi.listSources();
      const source = sources.items.find((s) => s.platform === platform)
        ?? await resourcesApi.createSource({ name: "Kênh của tôi", platform });
      const created = await resourcesApi.createResource({
        source_instance_id: source.id, platform, input_ref: inputValue.trim(),
        display_name: displayName.trim() || undefined,
      });
      setItems((cur) => [created, ...cur]);
      setInputValue("");
      setDisplayName("");
      toast.success(created.execution_mode === "fallback_local"
        ? "Đã thêm kênh. Thu thập dữ liệu đang được lên lịch."
        : "Đã thêm kênh");
    } catch (e) {
      toast.error(errorMessage(e, "Không thêm được kênh. Hãy thử lại."));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string, label: string) => {
    setDeletingId(id);
    try {
      await resourcesApi.deleteResource(id);
      setItems((cur) => cur.filter((r) => r.id !== id));
      toast.success(`Đã xóa kênh ${label}`);
    } catch (e) {
      toast.error(errorMessage(e, "Không xóa được kênh. Hãy thử lại."));
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div>
      <PageHeader title="Kênh của tôi"
        description="Tự khai báo kênh/trang bạn quản lý. Dữ liệu này chỉ ghi vào danh mục — Tool dự phòng sẽ kéo kênh này về chạy." />

      <Section title="Thêm kênh">
        <form className="max-w-xl space-y-4" onSubmit={(e) => { e.preventDefault(); void handleAdd(); }}>
          <Field label="Nền tảng">
            {(p) => (
              <Select value={platform} onValueChange={setPlatform}>
                <SelectTrigger {...p} className="md:max-w-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {platforms.map((pl) => <SelectItem key={pl} value={pl}>{PLATFORM_LABEL[pl] ?? pl}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
          </Field>
          <Field label="Link hoặc tên kênh" hint="Dán link trang/kênh, hoặc nhập tên định danh.">
            {(p) => <Input {...p} value={inputValue} onChange={(e) => setInputValue(e.target.value)} placeholder="https://facebook.com/ten-trang" />}
          </Field>
          <Field label="Tên hiển thị" optional>
            {(p) => <Input {...p} value={displayName} onChange={(e) => setDisplayName(e.target.value)} />}
          </Field>
          <Button type="submit" disabled={saving || !platform || !inputValue.trim()}>
            <Plus aria-hidden /> {saving ? "Đang thêm…" : "Thêm kênh"}
          </Button>
        </form>
      </Section>

      <Section title={`Kênh của bạn (${items.length})`}>
        {loadError && <Notice tone="error" className="mb-4">{loadError}</Notice>}
        {loading ? (
          <p role="status" className="py-6 text-sm text-muted-foreground">Đang tải…</p>
        ) : items.length === 0 ? (
          <EmptyState icon={Database} title="Chưa có kênh nào">Thêm kênh đầu tiên ở biểu mẫu phía trên.</EmptyState>
        ) : (
          <ul className="divide-y divide-border">
            {items.map((r) => {
              const label = r.display_name || r.username || r.input_ref;
              return (
                <li key={r.id} className="flex items-center justify-between gap-3 py-3">
                  <div>
                    <p className="font-medium">{label}</p>
                    <p className="text-sm text-muted-foreground">{PLATFORM_LABEL[r.platform] ?? r.platform} · {r.country || "—"}</p>
                  </div>
                  <Button variant="outline" onClick={() => void handleDelete(r.id, label)}
                          disabled={deletingId === r.id} aria-label={`Xóa kênh ${label}`}>
                    <Trash2 aria-hidden /> {deletingId === r.id ? "Đang xóa…" : "Xóa"}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </Section>
    </div>
  );
}
