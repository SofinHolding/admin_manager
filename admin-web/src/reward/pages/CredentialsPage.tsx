/**
 * Trang kết nối Discord — nhập token tài khoản người dùng + cấu hình lệnh slash để chạy job.
 *
 * Token chỉ dán một lần: PUT không bao giờ trả lại token, và trường token luôn để trống khi tải
 * lại trang (chỉ gửi khi người dùng nhập giá trị mới).
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, Trash2, XCircle } from "lucide-react";
import { credentialsApi, type CommandMeta, type CredentialInfo } from "../api";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import { Badge } from "../../components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";

const STATUS_BADGE: Record<string, { label: string; variant: "success" | "destructive" | "warning" | "secondary" }> = {
  valid: { label: "Đã xác thực", variant: "success" },
  invalid: { label: "Không hợp lệ", variant: "destructive" },
  unverified: { label: "Chưa xác thực", variant: "warning" },
  revoked: { label: "Đã thu hồi", variant: "destructive" },
};

interface FormState {
  token: string;
  guild_id: string;
  channel_id: string;
  command_name: string;
  confirm_mode: string;
  success_pattern: string;
  failure_pattern: string;
  leveling_bot_id: string;
  delay_ms: string;
  jitter_ms: string;
}

const EMPTY_FORM: FormState = {
  token: "",
  guild_id: "",
  channel_id: "",
  command_name: "give-xp",
  confirm_mode: "reply",
  success_pattern: "",
  failure_pattern: "",
  leveling_bot_id: "",
  delay_ms: "1200",
  jitter_ms: "400",
};

export default function CredentialsPage() {
  const [info, setInfo] = useState<CredentialInfo | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [command, setCommand] = useState<CommandMeta | null>(null);
  const [regexSample, setRegexSample] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const d = await credentialsApi.get();
      setInfo(d);
      if (d.exists) {
        setForm({
          token: "",
          guild_id: d.guild_id ?? "",
          channel_id: d.channel_id ?? "",
          command_name: d.command_name ?? "give-xp",
          confirm_mode: d.confirm_mode ?? "reply",
          success_pattern: d.success_pattern ?? "",
          failure_pattern: d.failure_pattern ?? "",
          leveling_bot_id: d.leveling_bot_id ?? "",
          delay_ms: String(d.delay_ms ?? 1200),
          jitter_ms: String(d.jitter_ms ?? 400),
        });
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const handleSave = async () => {
    if (!form.guild_id.trim() || !form.channel_id.trim() || !form.command_name.trim()) {
      toast.error("Guild ID, Channel ID và tên lệnh là bắt buộc");
      return;
    }
    setSaving(true);
    try {
      const result = await credentialsApi.put({
        token: form.token.trim() || undefined,
        guild_id: form.guild_id.trim(),
        channel_id: form.channel_id.trim(),
        command_name: form.command_name.trim(),
        confirm_mode: form.confirm_mode,
        success_pattern: form.success_pattern || undefined,
        failure_pattern: form.failure_pattern || undefined,
        leveling_bot_id: form.leveling_bot_id || undefined,
        delay_ms: Number(form.delay_ms) || undefined,
        jitter_ms: Number(form.jitter_ms) || undefined,
      });
      toast.success("Đã lưu cấu hình kết nối Discord");
      setCommand(result.command);
      set("token", "");
      await load();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const handleVerify = async () => {
    setVerifying(true);
    try {
      const result = await credentialsApi.verify();
      setCommand(result.command);
      toast.success(`Kết nối OK — Discord: ${result.discord_username}`);
      await load();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setVerifying(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm("Xoá cấu hình kết nối Discord? Job đang chạy/tạm dừng sẽ chặn thao tác này.")) return;
    try {
      await credentialsApi.delete();
      toast.success("Đã xoá cấu hình");
      setForm(EMPTY_FORM);
      setCommand(null);
      await load();
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  let regexTestResult: "match" | "no-match" | "invalid" | null = null;
  if (regexSample && form.success_pattern) {
    try {
      regexTestResult = new RegExp(form.success_pattern).test(regexSample) ? "match" : "no-match";
    } catch {
      regexTestResult = "invalid";
    }
  }

  const statusInfo = info?.status ? STATUS_BADGE[info.status] : undefined;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Kết nối Discord</h1>
          <p className="text-sm text-muted-foreground">
            Cấu hình tài khoản Discord dùng để chạy lệnh cộng điểm.
          </p>
        </div>
        {statusInfo && <Badge variant={statusInfo.variant}>{statusInfo.label}</Badge>}
      </div>

      <div className="glass flex items-start gap-3 rounded-xl border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
        <AlertTriangle className="mt-0.5 size-5 shrink-0" />
        <div>
          <p className="font-semibold">Cảnh báo vi phạm Điều khoản dịch vụ Discord</p>
          <p className="mt-1">
            Token tài khoản người dùng (user token) vi phạm ToS Discord và có thể khiến tài khoản bị khoá vĩnh
            viễn. Hãy dùng một tài khoản phụ, không dùng tài khoản chính.
          </p>
        </div>
      </div>

      {loading ? (
        <div className="text-sm text-muted-foreground">Đang tải…</div>
      ) : (
        <div className="glass grid gap-6 rounded-xl p-6 md:grid-cols-2">
          <div className="flex flex-col gap-1.5 md:col-span-2">
            <Label htmlFor="token">Discord token (dán một lần, không hiển thị lại)</Label>
            <Input
              id="token"
              type="password"
              autoComplete="off"
              value={form.token}
              onChange={(e) => set("token", e.target.value)}
              placeholder={info?.exists ? "•••••••• (để trống nếu không đổi token)" : "Dán token tài khoản Discord"}
            />
            {info?.discord_username && (
              <p className="text-xs text-muted-foreground">
                Tài khoản Discord đã xác thực: <span className="font-medium">{info.discord_username}</span>
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="guild_id">Guild ID</Label>
            <Input id="guild_id" value={form.guild_id} onChange={(e) => set("guild_id", e.target.value)} placeholder="123456789012345678" />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="channel_id">Channel ID</Label>
            <Input id="channel_id" value={form.channel_id} onChange={(e) => set("channel_id", e.target.value)} placeholder="123456789012345678" />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="command_name">Tên lệnh</Label>
            <Input id="command_name" value={form.command_name} onChange={(e) => set("command_name", e.target.value)} placeholder="give-xp" />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Chế độ xác nhận</Label>
            <Select value={form.confirm_mode} onValueChange={(v) => set("confirm_mode", v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="reply">reply — chờ bot trả lời</SelectItem>
                <SelectItem value="off">off — không chờ xác nhận</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="leveling_bot_id">ID bot leveling (tuỳ chọn)</Label>
            <Input id="leveling_bot_id" value={form.leveling_bot_id} onChange={(e) => set("leveling_bot_id", e.target.value)} placeholder="Dùng để liên kết reply theo bot_id" />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="delay_ms">Độ trễ giữa các lệnh (ms)</Label>
            <Input id="delay_ms" type="number" value={form.delay_ms} onChange={(e) => set("delay_ms", e.target.value)} />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="jitter_ms">Độ lệch ngẫu nhiên (ms)</Label>
            <Input id="jitter_ms" type="number" value={form.jitter_ms} onChange={(e) => set("jitter_ms", e.target.value)} />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="success_pattern">Success pattern (regex)</Label>
            <Textarea id="success_pattern" value={form.success_pattern} onChange={(e) => set("success_pattern", e.target.value)} placeholder="đã nhận|received \\+\\d+" />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="failure_pattern">Failure pattern (regex)</Label>
            <Textarea id="failure_pattern" value={form.failure_pattern} onChange={(e) => set("failure_pattern", e.target.value)} placeholder="lỗi|error|không tìm thấy" />
          </div>

          <div className="flex flex-col gap-1.5 md:col-span-2 rounded-lg border border-border-strong bg-elev-1 p-3">
            <Label htmlFor="regex_sample">Thử regex trên một câu mẫu</Label>
            <Input id="regex_sample" value={regexSample} onChange={(e) => setRegexSample(e.target.value)} placeholder="Dán nội dung reply mẫu của bot vào đây" />
            {regexTestResult && (
              <p className={`text-xs ${regexTestResult === "match" ? "text-success" : regexTestResult === "invalid" ? "text-destructive" : "text-warning"}`}>
                {regexTestResult === "match" && "✓ Khớp success_pattern"}
                {regexTestResult === "no-match" && "✗ Không khớp success_pattern"}
                {regexTestResult === "invalid" && "Regex không hợp lệ"}
              </p>
            )}
          </div>

          {command && (
            <div className="flex flex-col gap-1 rounded-lg border border-border-strong bg-elev-1 p-3 text-xs text-muted-foreground md:col-span-2">
              <p className="font-medium text-foreground">Metadata lệnh /{form.command_name}</p>
              <p>application_id: {command.application_id} · command_id: {command.command_id} · version: {command.version}</p>
              <p>option thành viên: {command.member_option_name} (type {command.member_option_type}) · option số điểm: {command.amount_option_name} (type {command.amount_option_type})</p>
            </div>
          )}

          {info?.last_error && (
            <p className="text-xs text-destructive md:col-span-2">Lỗi gần nhất: {info.last_error}</p>
          )}

          <div className="flex flex-wrap items-center gap-2 md:col-span-2">
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin" />}
              Lưu cấu hình
            </Button>
            <Button variant="outline" onClick={handleVerify} disabled={verifying || !info?.exists}>
              {verifying ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
              Kiểm tra kết nối
            </Button>
            {info?.exists && (
              <Button variant="destructive" onClick={handleDelete}>
                <Trash2 className="size-4" /> Xoá cấu hình
              </Button>
            )}
            {statusInfo?.variant === "success" && <CheckCircle2 className="size-4 text-success" />}
            {statusInfo?.variant === "destructive" && <XCircle className="size-4 text-destructive" />}
          </div>
        </div>
      )}
    </div>
  );
}
