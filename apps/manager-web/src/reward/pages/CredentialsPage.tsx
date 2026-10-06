/**
 * Kết nối Discord — nơi người vận hành cho hệ thống biết: gửi lệnh cộng điểm vào kênh nào, bằng tài khoản Discord nào.
 *
 * Gồm (1) danh sách tài khoản Discord (mã đăng nhập) xoay vòng khi chạy công việc, (2) nơi gửi lệnh (máy chủ, kênh, lệnh),
 * (3) cài đặt nâng cao. Mỗi lệnh /give-xp đi qua một tài khoản chọn NGẪU NHIÊN; tài khoản bị Discord từ chối sẽ tự bị loại
 * và mục đó được gửi lại bằng tài khoản khác. Mã đăng nhập chỉ dán MỘT lần: API không bao giờ trả lại, giao diện không hiện lại.
 */

import { useEffect, useState, type ComponentType } from "react";
import { toast } from "sonner";
import { Link } from "react-router-dom";
import {
  CircleCheck, CircleHelp, CircleX, Link2, Loader2, Plus, Power, RefreshCw, ShieldAlert, ShieldOff, Trash2,
} from "lucide-react";
import { credentialsApi, type CommandMeta, type CredentialInfo, type DiscordToken } from "../api";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Textarea } from "../../components/ui/textarea";
import { Badge } from "../../components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { EmptyState, Field, Notice, PageHeader, Section, TechDetails } from "../../components/kit";
import { ConfirmDialog, type ConfirmSpec, relativeTime } from "./rewardUi";

type BadgeVariant = "success" | "destructive" | "warning" | "secondary";
type Icon = ComponentType<{ className?: string }>;

/** Trạng thái của cả kết nối. */
const CONNECTION_STATUS: Record<string, { label: string; variant: BadgeVariant; icon: Icon }> = {
  valid: { label: "Đang hoạt động", variant: "success", icon: CircleCheck },
  unverified: { label: "Cần kiểm tra lại", variant: "warning", icon: CircleHelp },
  invalid: { label: "Không dùng được", variant: "destructive", icon: CircleX },
  revoked: { label: "Bị thu hồi", variant: "destructive", icon: ShieldOff },
};

/** Trạng thái của từng tài khoản Discord. */
const TOKEN_STATUS: Record<string, { label: string; variant: BadgeVariant; icon: Icon }> = {
  valid: { label: "Dùng được", variant: "success", icon: CircleCheck },
  unverified: { label: "Chưa kiểm tra", variant: "warning", icon: CircleHelp },
  invalid: { label: "Không dùng được", variant: "destructive", icon: CircleX },
  revoked: { label: "Bị thu hồi", variant: "destructive", icon: ShieldOff },
};

function StatusPill({ info }: { info: { label: string; variant: BadgeVariant; icon: Icon } }) {
  const I = info.icon;
  return <Badge variant={info.variant}><I className="size-3.5 shrink-0" aria-hidden />{info.label}</Badge>;
}

interface FormState {
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

// Mặc định khớp câu trả lời thật của Axolink Management ("N XP has been given to @user") — người dùng
// không cần tự gõ regex. Áp dụng cả khi tạo mới (EMPTY_FORM) lẫn khi tải lại 1 kết nối cũ đang
// thiếu pattern (load() bên dưới, dùng `||` chứ không phải `??` để tự phục hồi cả trường hợp rỗng).
const DEFAULT_SUCCESS_PATTERN = "\\d+\\s*XP has been given to";

const EMPTY_FORM: FormState = {
  guild_id: "",
  channel_id: "",
  command_name: "give-xp",
  confirm_mode: "reply",
  success_pattern: DEFAULT_SUCCESS_PATTERN,
  failure_pattern: "",
  leveling_bot_id: "",
  delay_ms: "1200",
  jitter_ms: "400",
};

type Outcome = { tone: "success" | "warning" | "error"; title: string; text?: string } | null;

const isNonNegInt = (v: string) => v.trim() === "" || /^\d+$/.test(v.trim());

export default function CredentialsPage() {
  const [info, setInfo] = useState<CredentialInfo | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [command, setCommand] = useState<CommandMeta | null>(null);
  const [regexSample, setRegexSample] = useState("");
  const [newToken, setNewToken] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [adding, setAdding] = useState(false);
  const [busyToken, setBusyToken] = useState<number | null>(null);
  const [formTried, setFormTried] = useState(false);
  const [tokenTried, setTokenTried] = useState(false);
  const [formOutcome, setFormOutcome] = useState<Outcome>(null);
  const [tokenOutcome, setTokenOutcome] = useState<Outcome>(null);
  const [confirm, setConfirm] = useState<ConfirmSpec | null>(null);

  const load = async () => {
    setLoading(true);
    setLoadFailed(false);
    try {
      const d = await credentialsApi.get();
      setInfo(d);
      if (d.exists) {
        setForm({
          guild_id: d.guild_id ?? "",
          channel_id: d.channel_id ?? "",
          command_name: d.command_name ?? "give-xp",
          confirm_mode: d.confirm_mode ?? "reply",
          success_pattern: d.success_pattern || DEFAULT_SUCCESS_PATTERN,
          failure_pattern: d.failure_pattern ?? "",
          leveling_bot_id: d.leveling_bot_id ?? "",
          delay_ms: String(d.delay_ms ?? 1200),
          jitter_ms: String(d.jitter_ms ?? 400),
        });
      }
    } catch (err) {
      setLoadFailed(true);
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

  const errors = {
    guild_id: form.guild_id.trim() ? null : "Nhập mã máy chủ Discord.",
    channel_id: form.channel_id.trim() ? null : "Nhập mã kênh Discord.",
    command_name: form.command_name.trim() ? null : "Nhập tên lệnh, ví dụ give-xp.",
    delay_ms: isNonNegInt(form.delay_ms) ? null : "Nhập số nguyên từ 0 trở lên.",
    jitter_ms: isNonNegInt(form.jitter_ms) ? null : "Nhập số nguyên từ 0 trở lên.",
  };
  const shown = <K extends keyof typeof errors>(k: K) => (formTried ? errors[k] : null);
  const formValid = Object.values(errors).every((e) => !e);

  const handleSave = async () => {
    setFormTried(true);
    setFormOutcome(null);
    if (!formValid) {
      setFormOutcome({ tone: "error", title: "Chưa lưu được", text: "Sửa các ô báo lỗi phía trên rồi bấm Lưu cấu hình lại." });
      return;
    }
    setSaving(true);
    try {
      const result = await credentialsApi.put({
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
      setCommand(result.command);
      if (result.status === "valid") {
        setFormOutcome({ tone: "success", title: "Đã lưu và kết nối được với Discord" });
        toast.success("Đã lưu cấu hình kết nối Discord");
      } else {
        setFormOutcome({
          tone: "warning", title: "Đã lưu, nhưng kết nối chưa dùng được",
          text: result.last_error || "Kiểm tra lại mã máy chủ, mã kênh và các tài khoản Discord ở phía trên.",
        });
      }
      await load();
    } catch (err) {
      setFormOutcome({ tone: "error", title: "Chưa lưu được cấu hình", text: (err as Error).message });
    } finally {
      setSaving(false);
    }
  };

  const handleVerify = async () => {
    setVerifying(true);
    setFormOutcome(null);
    try {
      const result = await credentialsApi.verify();
      setCommand(result.command);
      const ok = result.tokens.filter((t) => t.enabled && t.status === "valid").length;
      if (result.status === "valid") {
        setFormOutcome({ tone: "success", title: "Kết nối hoạt động", text: `${ok}/${result.tokens.length} tài khoản Discord dùng được.` });
        toast.success(`Kết nối OK — ${ok}/${result.tokens.length} tài khoản dùng được`);
      } else {
        setFormOutcome({
          tone: "error", title: "Không có tài khoản Discord nào dùng được",
          text: result.last_error || "Thêm một tài khoản mới hoặc kiểm tra lại mã máy chủ, mã kênh.",
        });
      }
      await load();
    } catch (err) {
      setFormOutcome({ tone: "error", title: "Chưa kiểm tra được kết nối", text: (err as Error).message });
    } finally {
      setVerifying(false);
    }
  };

  const handleAddToken = async () => {
    setTokenTried(true);
    setTokenOutcome(null);
    if (!newToken.trim()) return;
    setAdding(true);
    try {
      const r = await credentialsApi.addToken({ token: newToken.trim(), label: newLabel.trim() || undefined });
      if (r.command) setCommand(r.command);
      if (r.token.status === "valid") {
        const who = r.token.discord_username ?? r.token.label;
        setTokenOutcome({ tone: "success", title: `Đã thêm tài khoản ${who}`, text: "Discord xác nhận tài khoản này dùng được." });
        toast.success(`Đã thêm tài khoản: ${who}`);
      } else {
        setTokenOutcome({
          tone: "warning", title: "Đã thêm nhưng tài khoản chưa dùng được",
          text: r.token.last_error || "Discord chưa chấp nhận mã này. Kiểm tra lại mã rồi bấm Kiểm tra lại ở dòng tài khoản.",
        });
      }
      // Mã đăng nhập không được nằm lại trong ô nhập sau khi gửi.
      setNewToken("");
      setNewLabel("");
      setTokenTried(false);
      await load();
    } catch (err) {
      setTokenOutcome({ tone: "error", title: "Chưa thêm được tài khoản", text: (err as Error).message });
    } finally {
      setAdding(false);
    }
  };

  const tokenAction = async (t: DiscordToken, fn: () => Promise<unknown>, okMsg: string) => {
    setBusyToken(t.id);
    try {
      await fn();
      toast.success(okMsg);
      await load();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBusyToken(null);
    }
  };

  const askDeleteToken = (t: DiscordToken) => {
    const name = t.label || t.discord_username || `#${t.id}`;
    setConfirm({
      title: `Xóa tài khoản ${name}?`,
      body: "Tài khoản này sẽ bị xóa khỏi danh sách xoay vòng và mã đăng nhập đã lưu bị hủy. Các công việc sau này không dùng nó nữa; muốn dùng lại bạn phải nhập mã mới.",
      confirmLabel: "Xóa tài khoản",
      destructive: true,
      onConfirm: () => tokenAction(t, () => credentialsApi.deleteToken(t.id), "Đã xóa tài khoản"),
    });
  };

  const doDelete = async () => {
    try {
      await credentialsApi.delete();
      toast.success("Đã xóa kết nối Discord");
      setForm(EMPTY_FORM);
      setCommand(null);
      setFormOutcome(null);
      setFormTried(false);
      await load();
    } catch (err) {
      setFormOutcome({ tone: "error", title: "Chưa xóa được kết nối", text: (err as Error).message });
    }
  };

  const askDelete = () => setConfirm({
    title: "Xóa kết nối Discord?",
    body: "Toàn bộ cấu hình kênh và mọi mã đăng nhập Discord đã lưu sẽ bị xóa. Muốn chạy công việc lại bạn phải thiết lập từ đầu. Không xóa được khi còn công việc đang chạy hoặc đang tạm dừng.",
    confirmLabel: "Xóa kết nối",
    destructive: true,
    onConfirm: doDelete,
  });

  let regexTestResult: "match" | "no-match" | "invalid" | null = null;
  if (regexSample && form.success_pattern) {
    try {
      regexTestResult = new RegExp(form.success_pattern).test(regexSample) ? "match" : "no-match";
    } catch {
      regexTestResult = "invalid";
    }
  }

  const statusInfo = info?.exists && info.status ? CONNECTION_STATUS[info.status] : undefined;
  const tokens = info?.tokens ?? [];
  const usableTokens = tokens.filter((t) => t.enabled && t.status === "valid").length;

  return (
    <div>
      <PageHeader
        title="Kết nối Discord"
        description="Cho hệ thống biết gửi lệnh cộng điểm vào kênh Discord nào và bằng tài khoản nào. Mã đăng nhập Discord được mã hóa khi lưu và không bao giờ hiện lại sau khi bạn nhập."
        actions={statusInfo && <StatusPill info={statusInfo} />}
      />

      <Notice tone="warning" title="Dùng tài khoản Discord phụ, không dùng tài khoản chính" className="mb-6">
        Dùng mã đăng nhập của tài khoản cá nhân để gửi lệnh vi phạm điều khoản dịch vụ của Discord và có thể khiến tài khoản bị khóa vĩnh viễn.
        Hãy chỉ thêm tài khoản phụ mà bạn sẵn sàng mất.
      </Notice>

      {loading ? (
        <p role="status" className="flex items-center gap-2 text-muted-foreground"><Loader2 className="size-4" aria-hidden /> Đang tải kết nối…</p>
      ) : loadFailed && !info ? (
        <Notice tone="error" title="Chưa tải được kết nối Discord" action={<Button variant="outline" onClick={load}>Thử lại</Button>}>
          Kiểm tra kết nối mạng rồi bấm Thử lại.
        </Notice>
      ) : (
        <>
          {/* ── Tài khoản Discord ─────────────────────────────────────────── */}
          <Section
            id="tai-khoan"
            title={`Tài khoản Discord (${usableTokens}/${tokens.length} dùng được)`}
            description="Mỗi lệnh cộng điểm đi qua một tài khoản chọn ngẫu nhiên, nên thêm nhiều tài khoản sẽ gửi nhanh hơn và an toàn hơn. Tài khoản bị Discord từ chối sẽ tự bị loại và lệnh được gửi lại bằng tài khoản khác."
          >
            {tokens.length === 0 ? (
              <EmptyState icon={Link2} title="Chưa có tài khoản Discord nào">
                Thêm ít nhất một tài khoản ở biểu mẫu bên dưới để có thể chạy công việc phân phối.
              </EmptyState>
            ) : (
              <ul className="divide-y divide-border border-y border-border">
                {tokens.map((t) => {
                  const st = TOKEN_STATUS[t.status] ?? TOKEN_STATUS.unverified;
                  const name = t.label || t.discord_username || `#${t.id}`;
                  const busy = busyToken === t.id;
                  return (
                    <li key={t.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
                      <div className="min-w-0 flex-1 basis-56">
                        <p className="break-words font-semibold">
                          {name}
                          {t.discord_username && t.label && t.label !== t.discord_username && (
                            <span className="ml-2 text-sm font-normal text-muted-foreground">@{t.discord_username}</span>
                          )}
                        </p>
                        <p className="mt-1 flex flex-wrap items-center gap-2">
                          <StatusPill info={st} />
                          {!t.enabled && <Badge variant="secondary"><Power className="size-3.5" aria-hidden />Đang tắt</Badge>}
                          {t.verified_at && <span className="text-sm text-muted-foreground">kiểm tra {relativeTime(t.verified_at)}</span>}
                        </p>
                        {t.last_error && t.status !== "valid" && (
                          <p className="mt-1 break-words text-sm text-destructive">{t.last_error}</p>
                        )}
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="outline" disabled={busy}
                          aria-label={`Kiểm tra lại tài khoản ${name}`}
                          onClick={() => tokenAction(t, () => credentialsApi.verifyToken(t.id), "Đã kiểm tra lại tài khoản")}
                        >
                          {busy ? <Loader2 aria-hidden /> : <RefreshCw aria-hidden />} Kiểm tra lại
                        </Button>
                        <Button
                          variant="outline" disabled={busy}
                          aria-label={`${t.enabled ? "Tắt" : "Bật"} tài khoản ${name}`}
                          title={t.enabled ? "Tắt: tài khoản không tham gia xoay vòng nhưng vẫn được lưu" : "Bật lại để tham gia xoay vòng"}
                          onClick={() => tokenAction(
                            t, () => credentialsApi.patchToken(t.id, { enabled: !t.enabled }),
                            t.enabled ? "Đã tắt tài khoản" : "Đã bật tài khoản",
                          )}
                        >
                          <Power aria-hidden /> {t.enabled ? "Tắt" : "Bật"}
                        </Button>
                        <Button variant="outline" disabled={busy} aria-label={`Xóa tài khoản ${name}`} onClick={() => askDeleteToken(t)}
                                className="text-destructive hover:text-destructive">
                          <Trash2 aria-hidden /> Xóa
                        </Button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}

            <div className="mt-6 max-w-2xl">
              <h3>Thêm tài khoản Discord</h3>
              <p className="reading mt-1 text-sm text-muted-foreground">
                Dán mã đăng nhập Discord của tài khoản phụ. Hệ thống mã hóa, kiểm tra với Discord rồi chỉ giữ lại tên tài khoản; mã gốc không hiện lại ở bất kỳ đâu.
              </p>
              <div className="mt-4 grid gap-4 md:grid-cols-[1fr_2fr]">
                <Field label="Tên gọi" optional hint="Để bạn nhận ra tài khoản, ví dụ Tài khoản phụ 1.">
                  {(p) => <Input {...p} value={newLabel} onChange={(e) => setNewLabel(e.target.value)} />}
                </Field>
                <Field
                  label="Mã đăng nhập Discord"
                  hint="Dán mã vào đây. Mã chỉ hiển thị khi bạn nhập, sau khi thêm sẽ không xem lại được."
                  error={tokenTried && !newToken.trim() ? "Hãy dán mã đăng nhập Discord." : null}
                >
                  {(p) => <Input {...p} type="password" autoComplete="off" value={newToken} onChange={(e) => setNewToken(e.target.value)} />}
                </Field>
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <Button onClick={handleAddToken} disabled={adding}>
                  {adding ? <Loader2 aria-hidden /> : <Plus aria-hidden />} Thêm tài khoản
                </Button>
                {adding && <p role="status" className="text-sm text-muted-foreground">Đang thêm và hỏi Discord xem mã có dùng được không…</p>}
              </div>
              {tokenOutcome && (
                <Notice className="mt-4" tone={tokenOutcome.tone} title={tokenOutcome.title}>{tokenOutcome.text}</Notice>
              )}
            </div>
          </Section>

          {/* ── Nơi gửi lệnh ──────────────────────────────────────────────── */}
          <Section id="noi-gui" title="Nơi gửi lệnh" description="Máy chủ và kênh Discord có bot phát điểm, cùng tên lệnh cộng điểm.">
            <div className="grid max-w-3xl gap-4 md:grid-cols-2">
              <Field label="Mã máy chủ Discord (Guild ID)" hint="Dãy số dài 17–20 chữ số. Trong Discord bật Chế độ nhà phát triển, bấm chuột phải vào máy chủ rồi chọn Sao chép ID." error={shown("guild_id")}>
                {(p) => <Input {...p} inputMode="numeric" value={form.guild_id} onChange={(e) => set("guild_id", e.target.value)} placeholder="123456789012345678" />}
              </Field>
              <Field label="Mã kênh Discord (Channel ID)" hint="Kênh nơi bot nhận lệnh cộng điểm. Lấy ID tương tự như máy chủ." error={shown("channel_id")}>
                {(p) => <Input {...p} inputMode="numeric" value={form.channel_id} onChange={(e) => set("channel_id", e.target.value)} placeholder="123456789012345678" />}
              </Field>
              <Field label="Tên lệnh" hint="Thường giữ nguyên give-xp." error={shown("command_name")}>
                {(p) => <Input {...p} value={form.command_name} onChange={(e) => set("command_name", e.target.value)} placeholder="give-xp" />}
              </Field>
              <Field label="Cách xác nhận kết quả" hint="Chờ bot trả lời giúp biết chắc điểm đã được cộng; chọn không chờ chỉ khi bot không trả lời.">
                {(p) => (
                  <Select value={form.confirm_mode} onValueChange={(v) => set("confirm_mode", v)}>
                    <SelectTrigger {...p}><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="reply">Chờ bot trả lời (khuyên dùng)</SelectItem>
                      <SelectItem value="off">Không chờ xác nhận</SelectItem>
                    </SelectContent>
                  </Select>
                )}
              </Field>
              <Field label="Độ trễ giữa các lệnh của mỗi tài khoản (ms)" hint="Một tài khoản không gửi nhanh hơn mức này. Mặc định 1200." error={shown("delay_ms")}>
                {(p) => <Input {...p} type="number" min={0} value={form.delay_ms} onChange={(e) => set("delay_ms", e.target.value)} />}
              </Field>
              <Field label="Độ lệch ngẫu nhiên (ms)" hint="Cộng thêm thời gian chờ ngẫu nhiên để lệnh không đều đặn như máy. Mặc định 400." error={shown("jitter_ms")}>
                {(p) => <Input {...p} type="number" min={0} value={form.jitter_ms} onChange={(e) => set("jitter_ms", e.target.value)} />}
              </Field>
            </div>
          </Section>

          {/* ── Nâng cao ─────────────────────────────────────────────────── */}
          <Section id="nang-cao" title="Cài đặt nâng cao" description="Chỉ cần đổi khi bot của bạn trả lời khác mặc định.">
            <div className="grid max-w-3xl gap-4 md:grid-cols-2">
              <Field label="Mã bot phát điểm" optional hint="Giúp nhận đúng câu trả lời của bot khi kênh có nhiều bot.">
                {(p) => <Input {...p} value={form.leveling_bot_id} onChange={(e) => set("leveling_bot_id", e.target.value)} />}
              </Field>
              <div className="hidden md:block" />
              <Field label="Mẫu nhận biết thành công (regex)" hint="Câu trả lời của bot khớp mẫu này thì tính là đã cộng điểm. Mặc định đã khớp với bot hiện dùng.">
                {(p) => <Textarea {...p} className="font-mono text-sm" value={form.success_pattern} onChange={(e) => set("success_pattern", e.target.value)} />}
              </Field>
              <Field label="Mẫu nhận biết thất bại (regex)" optional hint="Câu trả lời khớp mẫu này thì tính là thất bại.">
                {(p) => <Textarea {...p} className="font-mono text-sm" value={form.failure_pattern} onChange={(e) => set("failure_pattern", e.target.value)} placeholder="lỗi|error|không tìm thấy" />}
              </Field>
              <div className="md:col-span-2">
                <Field label="Thử mẫu thành công trên một câu mẫu" optional hint="Dán một câu trả lời thật của bot để xem mẫu có khớp không.">
                  {(p) => <Input {...p} value={regexSample} onChange={(e) => setRegexSample(e.target.value)} />}
                </Field>
                {regexTestResult && (
                  <p role="status" className={`mt-2 flex items-center gap-1.5 text-sm font-medium ${regexTestResult === "match" ? "text-success" : regexTestResult === "invalid" ? "text-destructive" : "text-warning"}`}>
                    {regexTestResult === "match" && <><CircleCheck className="size-4" aria-hidden /> Khớp: câu này sẽ được tính là thành công.</>}
                    {regexTestResult === "no-match" && <><CircleX className="size-4" aria-hidden /> Không khớp: câu này sẽ không được tính là thành công.</>}
                    {regexTestResult === "invalid" && <><ShieldAlert className="size-4" aria-hidden /> Mẫu không hợp lệ, hãy kiểm tra lại cú pháp.</>}
                  </p>
                )}
              </div>
            </div>
          </Section>

          {/* ── Hành động ────────────────────────────────────────────────── */}
          <Section id="hanh-dong">
            {info?.last_error && (
              <Notice className="mb-4" tone="error" title="Lần kiểm tra gần nhất thất bại">{info.last_error}</Notice>
            )}
            {formOutcome && (
              <Notice className="mb-4" tone={formOutcome.tone} title={formOutcome.title}>{formOutcome.text}</Notice>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={handleSave} disabled={saving}>
                {saving && <Loader2 aria-hidden />} Lưu cấu hình
              </Button>
              <Button variant="outline" onClick={handleVerify} disabled={verifying || !info?.exists}>
                {verifying ? <Loader2 aria-hidden /> : <RefreshCw aria-hidden />} Kiểm tra kết nối
              </Button>
              {info?.exists && (
                <Button variant="outline" onClick={askDelete} className="text-destructive hover:text-destructive">
                  <Trash2 aria-hidden /> Xóa kết nối
                </Button>
              )}
            </div>
            <p role="status" className="mt-2 min-h-5 text-sm text-muted-foreground">
              {saving ? "Đang lưu và kiểm tra với Discord…" : verifying ? "Đang hỏi Discord xem từng tài khoản có dùng được không…" : ""}
            </p>
            {!info?.exists && (
              <p className="mt-2 text-sm text-muted-foreground">Chưa lưu cấu hình thì chưa kiểm tra kết nối được.</p>
            )}
            {info?.exists && statusInfo?.variant === "success" && (
              <p className="mt-4 text-sm">
                Kết nối đã sẵn sàng. <Link to="/reward/jobs/new" className="font-medium text-primary-text underline underline-offset-2">Tạo công việc phân phối</Link>
              </p>
            )}
            {command && (
              <TechDetails className="mt-4 max-w-3xl">
                <p className="font-medium">Thông tin lệnh /{form.command_name}</p>
                <p className="mt-1 break-all text-muted-foreground">application_id: {command.application_id} · command_id: {command.command_id} · version: {command.version}</p>
                <p className="mt-1 text-muted-foreground">Ô thành viên: {command.member_option_name} (type {command.member_option_type}) · Ô số điểm: {command.amount_option_name} (type {command.amount_option_type})</p>
              </TechDetails>
            )}
          </Section>
        </>
      )}

      <ConfirmDialog spec={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}
