/**
 * Trang tạo job — dán/kéo-thả danh sách username|point, xem trước, kiểm tra, tạo job.
 */

import { useMemo, useState, type DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { AlertCircle, Loader2, UploadCloud } from "lucide-react";
import { jobsApi, type JobOverrides, type ValidationIssue } from "../api";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import { Badge } from "../../components/ui/badge";

interface PreviewRow {
  index: number;
  raw: string;
  username: string;
  point: number | null;
  error: string | null;
}

/** Xem trước cục bộ ở trình duyệt — kiểm tra sơ bộ trước khi gọi API `validate`, không thay thế nó. */
function parsePreview(rawText: string): PreviewRow[] {
  const lines = rawText.split("\n").map((l) => l.trim()).filter((l) => l.length > 0);
  return lines.map((raw, index) => {
    const parts = raw.split(/[|,\t]|\s{2,}| (?=\d+$)/).map((p) => p.trim()).filter(Boolean);
    if (parts.length < 2) {
      return { index, raw, username: raw, point: null, error: "Thiếu điểm — định dạng username|point" };
    }
    const username = parts[0];
    const point = Number(parts[parts.length - 1]);
    if (!username) return { index, raw, username, point: null, error: "Thiếu username" };
    if (!Number.isFinite(point) || point <= 0) {
      return { index, raw, username, point: null, error: "Điểm phải là số nguyên dương" };
    }
    return { index, raw, username, point, error: null };
  });
}

export default function JobNewPage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [rawText, setRawText] = useState("");
  const [overrideEnabled, setOverrideEnabled] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, string>>({
    guild_id: "",
    channel_id: "",
    command_name: "",
    confirm_mode: "",
    delay_ms: "",
    jitter_ms: "",
    max_item_retries: "",
    unknown_pause_threshold: "",
  });
  const [serverIssues, setServerIssues] = useState<ValidationIssue[]>([]);
  const [createdJobId, setCreatedJobId] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [creating, setCreating] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const preview = useMemo(() => parsePreview(rawText), [rawText]);
  const errorCount = preview.filter((r) => r.error).length;
  const previewCommands = preview.filter((r) => !r.error).slice(0, 5);

  const buildOverrides = (): JobOverrides | undefined => {
    if (!overrideEnabled) return undefined;
    const out: JobOverrides = {};
    if (overrides.guild_id) out.guild_id = overrides.guild_id;
    if (overrides.channel_id) out.channel_id = overrides.channel_id;
    if (overrides.command_name) out.command_name = overrides.command_name;
    if (overrides.confirm_mode) out.confirm_mode = overrides.confirm_mode;
    if (overrides.delay_ms) out.delay_ms = Number(overrides.delay_ms);
    if (overrides.jitter_ms) out.jitter_ms = Number(overrides.jitter_ms);
    if (overrides.max_item_retries) out.max_item_retries = Number(overrides.max_item_retries);
    if (overrides.unknown_pause_threshold) out.unknown_pause_threshold = Number(overrides.unknown_pause_threshold);
    return out;
  };

  const handleFile = async (file: File) => {
    const text = await file.text();
    setRawText((prev) => (prev ? `${prev}\n${text}` : text));
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  };

  const handleCheck = async () => {
    if (!name.trim() || !rawText.trim()) {
      toast.error("Nhập tên job và danh sách trước khi kiểm tra");
      return;
    }
    if (createdJobId) {
      navigate(`/reward/jobs/${createdJobId}`);
      return;
    }
    setChecking(true);
    try {
      const created = await jobsApi.create({ name: name.trim(), raw_text: rawText, overrides: buildOverrides() });
      setServerIssues(created.issues ?? []);
      setCreatedJobId(created.job_id);
      if (created.status === "validated") {
        toast.success(`Hợp lệ — ${created.total_items} dòng, không có lỗi`);
      } else {
        toast.warning(`Có ${created.issues.length} vấn đề — job đã tạo ở trạng thái ${created.status}`);
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setChecking(false);
    }
  };

  const handleCreate = async () => {
    if (createdJobId) {
      navigate(`/reward/jobs/${createdJobId}`);
      return;
    }
    if (!name.trim() || !rawText.trim()) {
      toast.error("Nhập tên job và danh sách");
      return;
    }
    setCreating(true);
    try {
      const created = await jobsApi.create({ name: name.trim(), raw_text: rawText, overrides: buildOverrides() });
      toast.success(`Đã tạo job "${name}" — ${created.total_items} mục`);
      navigate(`/reward/jobs/${created.job_id}`);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">Tạo job phân phối điểm</h1>
        <p className="text-sm text-muted-foreground">Dán danh sách username|point hoặc kéo-thả file .txt/.csv.</p>
      </div>

      <div className="glass flex flex-col gap-4 rounded-xl p-6">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="job_name">Tên job</Label>
          <Input id="job_name" value={name} onChange={(e) => setName(e.target.value)} placeholder="VD: Tuần 38 - Top voice" />
        </div>

        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          className={`flex flex-col gap-1.5 rounded-lg border-2 border-dashed p-3 transition-colors ${dragOver ? "border-primary bg-primary/5" : "border-border-strong"}`}
        >
          <Label htmlFor="raw_text" className="flex items-center gap-1.5">
            <UploadCloud className="size-4" /> Danh sách username|point (kéo-thả file .txt/.csv vào đây)
          </Label>
          <Textarea
            id="raw_text"
            className="min-h-[220px] font-mono text-xs"
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
            placeholder={"username1|100\nusername2|250\nusername3,50"}
          />
        </div>

        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span>
            <span className="font-medium">{preview.length}</span> dòng
          </span>
          {errorCount > 0 ? (
            <Badge variant="destructive">{errorCount} dòng lỗi</Badge>
          ) : (
            preview.length > 0 && <Badge variant="success">Định dạng hợp lệ</Badge>
          )}
        </div>

        {preview.length > 0 && (
          <div className="max-h-64 overflow-auto rounded-lg border border-border-strong">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-surface-2 text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">#</th>
                  <th className="px-3 py-2">Username</th>
                  <th className="px-3 py-2">Điểm</th>
                  <th className="px-3 py-2">Ghi chú</th>
                </tr>
              </thead>
              <tbody>
                {preview.map((row) => (
                  <tr key={row.index} className={row.error ? "bg-destructive/5" : ""}>
                    <td className="px-3 py-1.5 text-muted-foreground">{row.index + 1}</td>
                    <td className="px-3 py-1.5">{row.username}</td>
                    <td className="px-3 py-1.5">{row.point ?? "—"}</td>
                    <td className="px-3 py-1.5 text-destructive">{row.error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="glass flex flex-col gap-4 rounded-xl p-6">
        <label className="flex items-center gap-2 text-sm font-medium">
          <input type="checkbox" checked={overrideEnabled} onChange={(e) => setOverrideEnabled(e.target.checked)} className="rounded accent-primary" />
          Ghi đè cấu hình cho job này (mặc định lấy từ cấu hình kết nối Discord)
        </label>
        {overrideEnabled && (
          <div className="grid gap-3 md:grid-cols-4">
            {(["guild_id", "channel_id", "command_name", "confirm_mode", "delay_ms", "jitter_ms", "max_item_retries", "unknown_pause_threshold"] as const).map((key) => (
              <div key={key} className="flex flex-col gap-1">
                <Label htmlFor={`ov_${key}`} className="text-xs">{key}</Label>
                <Input id={`ov_${key}`} value={overrides[key]} onChange={(e) => setOverrides((o) => ({ ...o, [key]: e.target.value }))} />
              </div>
            ))}
          </div>
        )}
      </div>

      {previewCommands.length > 0 && (
        <div className="glass rounded-xl p-4 text-xs text-muted-foreground">
          <p className="mb-2 font-medium text-foreground">Xem trước 5 lệnh sẽ gửi (0 request):</p>
          <ul className="flex flex-col gap-1 font-mono">
            {previewCommands.map((row) => (
              <li key={row.index}>/{overrides.command_name || "give-xp"} member:{row.username} amount:{row.point}</li>
            ))}
          </ul>
        </div>
      )}

      {serverIssues.length > 0 && (
        <div className="glass flex flex-col gap-2 rounded-xl border border-warning/40 p-4">
          <p className="flex items-center gap-1.5 text-sm font-medium text-warning"><AlertCircle className="size-4" /> Vấn đề từ máy chủ</p>
          <ul className="flex flex-col gap-1 text-xs">
            {serverIssues.map((issue, i) => (
              <li key={i}>Dòng {issue.row_index + 1}: [{issue.severity}] {issue.message}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex gap-2">
        <Button variant="outline" onClick={handleCheck} disabled={checking || creating}>
          {checking && <Loader2 className="size-4 animate-spin" />}
          {createdJobId ? "Xem kết quả kiểm tra" : "Kiểm tra"}
        </Button>
        <Button onClick={handleCreate} disabled={creating || checking}>
          {creating && <Loader2 className="size-4 animate-spin" />}
          {createdJobId ? "Mở job đã tạo" : "Tạo job"}
        </Button>
      </div>
    </div>
  );
}
