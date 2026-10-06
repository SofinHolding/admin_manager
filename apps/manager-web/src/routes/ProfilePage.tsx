/**
 * Trang hồ sơ cá nhân — xem thông tin, đổi mật khẩu, liên kết công cụ đẩy dữ liệu.
 *
 * Đổi mật khẩu cần xác thực mã 6 số gửi qua email TRƯỚC khi cho nhập mật khẩu mới.
 * Luồng: Gửi mã → Nhập mã + mật khẩu mới → Đổi thành công → Đăng nhập lại.
 * Đổi email: email mới ở trạng thái CHƯA xác thực và server gửi lại thư xác thực tới email mới.
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Copy, Link2, Send, Trash2 } from "lucide-react";
import { useAuth } from "../auth";
import { linksApi, profileApi, type ViewerLink } from "../api";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { EmptyState, Field, Notice, PageHeader, Section } from "../components/kit";
import {
  PASSWORD_RULE, PasswordField, Spinner, formError, passwordLengthError, roleAbout, roleLabel,
  type FriendlyError,
} from "../components/authKit";

export default function ProfilePage() {
  const { user } = useAuth();

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title="Hồ sơ" description="Xem thông tin tài khoản, đổi mật khẩu và liên kết công cụ đẩy dữ liệu của bạn." />
      {user && <ProfileForm user={user} />}
      <ChangePasswordForm />
      <ToolLinksCard />
    </div>
  );
}

// ── Thông tin tài khoản ─────────────────────────────────────────────────────

function ProfileForm({ user }: { user: { username: string; email: string; role: string } }) {
  const [username, setUsername] = useState(user.username);
  const [email, setEmail] = useState(user.email);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<FriendlyError | null>(null);
  const [saved, setSaved] = useState<{ usernameChanged: boolean; emailChanged: boolean; email: string } | null>(null);

  const hasChanges = username !== user.username || email !== user.email;

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!hasChanges) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const data: Record<string, string> = {};
      if (username !== user.username) data.username = username;
      if (email !== user.email) data.email = email;
      await profileApi.updateProfile(data);
      setSaved({ usernameChanged: "username" in data, emailChanged: "email" in data, email });
    } catch (err) {
      setError(formError(err, "Chưa lưu được thay đổi"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Section title="Thông tin tài khoản" description="Tên đăng nhập và email dùng để đăng nhập và nhận thư từ hệ thống.">
      <form onSubmit={handleSave} noValidate className="flex flex-col gap-5">
        {error && <Notice tone="error" title={error.title}>{error.body}</Notice>}
        {saved?.emailChanged && (
          <Notice tone="warning" title="Email mới chưa được xác thực">
            Chúng tôi đã gửi thư xác thực tới <strong className="break-all">{saved.email}</strong>. Mở thư và bấm vào
            liên kết trong thư để xác thực. Kiểm tra cả mục thư rác nếu chưa thấy.
          </Notice>
        )}
        {saved && !saved.emailChanged && (
          <Notice tone="success" title="Đã lưu thay đổi">Tải lại trang để thấy tên mới ở mọi nơi.</Notice>
        )}
        {saved?.emailChanged && saved.usernameChanged && (
          <Notice tone="success" title="Đã lưu tên đăng nhập">Tải lại trang để thấy tên mới ở mọi nơi.</Notice>
        )}

        <Field label="Tên đăng nhập" hint="3 đến 30 ký tự.">
          {(p) => (
            <Input {...p} name="username" autoComplete="username" autoCapitalize="none" spellCheck={false}
                   value={username} onChange={(e) => setUsername(e.target.value)} minLength={3} maxLength={30} />
          )}
        </Field>

        <Field label="Email" hint="Nếu đổi email, email mới sẽ ở trạng thái chưa xác thực cho tới khi bạn bấm liên kết trong thư chúng tôi gửi tới địa chỉ đó.">
          {(p) => (
            <Input {...p} name="email" type="email" autoComplete="email" autoCapitalize="none" spellCheck={false}
                   value={email} onChange={(e) => setEmail(e.target.value)} />
          )}
        </Field>

        <dl>
          <dt className="text-sm font-semibold">Vai trò</dt>
          <dd className="mt-1">
            <span className="font-medium">{roleLabel(user.role)}</span>
            <span className="reading mt-0.5 block text-sm text-muted-foreground">{roleAbout(user.role)}</span>
            <span className="mt-0.5 block text-sm text-muted-foreground">Vai trò do quản trị viên cấp; bạn không tự đổi được.</span>
          </dd>
        </dl>

        <div>
          <Button type="submit" size="lg" disabled={saving || !hasChanges}>
            {saving && <Spinner />}
            Lưu thay đổi
          </Button>
        </div>
      </form>
    </Section>
  );
}

// ── Đổi mật khẩu ────────────────────────────────────────────────────────────

function ChangePasswordForm() {
  const { logout } = useAuth();
  const [step, setStep] = useState<"idle" | "code_sent" | "done">("idle");
  const [sending, setSending] = useState(false);
  const [changing, setChanging] = useState(false);
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<FriendlyError | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [tried, setTried] = useState(false);

  const handleSendCode = async () => {
    setSending(true);
    setError(null);
    setInfo(null);
    try {
      await profileApi.sendChangeCode();
      setStep("code_sent");
      setInfo("Đã gửi mã 6 số về email của bạn. Mã hết hạn sau 10 phút.");
    } catch (e) {
      setError(formError(e, "Chưa gửi được mã"));
    } finally {
      setSending(false);
    }
  };

  const lengthError = passwordLengthError(newPassword, tried);
  const mismatch = confirmPassword.length > 0 && confirmPassword !== newPassword;

  const handleChange = async (e: React.FormEvent) => {
    e.preventDefault();
    setTried(true);
    if (code.length !== 6 || passwordLengthError(newPassword, true) || newPassword !== confirmPassword) return;
    setChanging(true);
    setError(null);
    try {
      await profileApi.changePassword(code, newPassword);
      setStep("done");
    } catch (err) {
      setError(formError(err, "Chưa đổi được mật khẩu"));
    } finally {
      setChanging(false);
    }
  };

  return (
    <Section title="Đổi mật khẩu" description="Để bảo vệ tài khoản, chúng tôi xác nhận bằng mã gửi qua email trước khi bạn đặt mật khẩu mới.">
      {step === "idle" && (
        <div className="flex flex-col gap-4">
          {error && <Notice tone="error" title={error.title}>{error.body}</Notice>}
          <ol className="list-decimal space-y-1 pl-6 text-[0.9375rem]">
            <li>Chúng tôi gửi mã 6 số về email của bạn.</li>
            <li>Bạn nhập mã đó và mật khẩu mới.</li>
            <li>Bạn đăng nhập lại bằng mật khẩu mới.</li>
          </ol>
          <div>
            <Button variant="outline" size="lg" onClick={handleSendCode} disabled={sending}>
              {sending ? <Spinner /> : <Send aria-hidden />}
              Gửi mã về email
            </Button>
          </div>
        </div>
      )}

      {step === "code_sent" && (
        <form onSubmit={handleChange} noValidate className="flex flex-col gap-5">
          {info && <Notice tone="info">{info}</Notice>}
          {error && <Notice tone="error" title={error.title}>{error.body}</Notice>}

          <Field label="Mã xác thực" hint="Mã 6 số trong thư. Chưa thấy thư thì xem mục thư rác hoặc bấm Gửi lại mã."
                 error={tried && code.length !== 6 ? "Nhập đủ 6 số." : null}>
            {(p) => (
              <Input {...p} name="code" inputMode="numeric" autoComplete="one-time-code" maxLength={6}
                     className="w-40 text-center font-mono text-lg tracking-widest"
                     value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} autoFocus />
            )}
          </Field>

          <PasswordField label="Mật khẩu mới" noun="mật khẩu mới" name="new-password" autoComplete="new-password"
                         value={newPassword} onChange={setNewPassword} hint={PASSWORD_RULE} error={lengthError} />

          <Field label="Nhập lại mật khẩu mới" error={mismatch ? "Hai mật khẩu chưa khớp. Nhập lại cho giống ô trên." : null}>
            {(p) => (
              <Input {...p} name="confirm-password" type="password" autoComplete="new-password"
                     value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} />
            )}
          </Field>

          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" size="lg" disabled={changing}>
              {changing && <Spinner />}
              Đổi mật khẩu
            </Button>
            <Button type="button" variant="ghost" size="lg" onClick={handleSendCode} disabled={sending}>
              Gửi lại mã
            </Button>
          </div>
        </form>
      )}

      {step === "done" && (
        <div className="flex flex-col gap-4">
          <Notice tone="success" title="Đã đổi mật khẩu">
            Các phiên đăng nhập cũ sẽ hết hiệu lực, nên bạn cần đăng nhập lại bằng mật khẩu mới.
          </Notice>
          <div>
            <Button size="lg" onClick={logout}>Đăng nhập lại</Button>
          </div>
        </div>
      )}
    </Section>
  );
}

// ── Công cụ của tôi ─────────────────────────────────────────────────────────

function fmtCountdown(ms: number): string {
  if (ms <= 0) return "Đã hết hạn";
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function errMsg(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function ToolLinksCard() {
  const [links, setLinks] = useState<ViewerLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [code, setCode] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const { items } = await linksApi.list();
      setLinks(items);
    } catch (e) {
      setError(errMsg(e, "Không tải được danh sách công cụ đã liên kết. Tải lại trang để thử lại."));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  useEffect(() => {
    if (!expiresAt) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [expiresAt]);

  const remainingMs = expiresAt ? new Date(expiresAt).getTime() - now : 0;

  const handleCreate = async () => {
    setCreating(true);
    setError(null);
    try {
      const res = await linksApi.createCode();
      setCode(res.code);
      setExpiresAt(res.expires_at);
      setNow(Date.now());
    } catch (e) {
      setError(errMsg(e, "Chưa tạo được mã liên kết. Thử lại sau ít phút."));
    } finally {
      setCreating(false);
    }
  };

  const handleCopy = () => {
    if (!code) return;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(code).then(() => toast.success("Đã sao chép mã")).catch(() => {});
    }
  };

  const handleRevoke = async (link: ViewerLink) => {
    if (!confirm(`Gỡ liên kết công cụ "${link.label || link.tool_user_id}"? Công cụ này sẽ không còn gắn với tài khoản của bạn.`)) return;
    setRevokingId(link.tool_user_id);
    setError(null);
    try {
      await linksApi.revoke(link.tool_user_id);
      setLinks((prev) => prev.filter((l) => l.tool_user_id !== link.tool_user_id));
      toast.success("Đã gỡ liên kết");
    } catch (e) {
      setError(errMsg(e, "Chưa gỡ được liên kết. Thử lại sau ít phút."));
    } finally {
      setRevokingId(null);
    }
  };

  const codeActive = !!code && remainingMs > 0;

  return (
    <Section title="Công cụ của tôi" description="Liên kết công cụ đẩy dữ liệu với tài khoản của bạn bằng một mã dùng một lần.">
      <div className="flex flex-col gap-5">
        {error && <Notice tone="error">{error}</Notice>}

        <div className="flex flex-col gap-3">
          {codeActive ? (
            <>
              <div className="flex flex-wrap items-center gap-3 border border-border-strong px-4 py-3">
                <span className="flex-1 font-mono text-2xl font-bold tracking-widest">{code}</span>
                <Button variant="outline" onClick={handleCopy}>
                  <Copy aria-hidden /> Sao chép mã
                </Button>
              </div>
              <p className="text-sm text-muted-foreground">
                Mã còn dùng được trong <span className="figure text-base">{fmtCountdown(remainingMs)}</span>. Dán mã vào công cụ để hoàn tất liên kết.
              </p>
            </>
          ) : (
            <>
              {code && <Notice tone="warning">Mã đã hết hạn. Tạo mã mới để liên kết.</Notice>}
              <p className="reading text-[0.9375rem] text-muted-foreground">
                Tạo mã, rồi dán vào công cụ để gắn dữ liệu nó thu thập với tài khoản của bạn. Mã hết hạn sau 15 phút.
              </p>
              <div>
                <Button variant="outline" size="lg" onClick={handleCreate} disabled={creating}>
                  {creating ? <Spinner /> : <Link2 aria-hidden />}
                  Tạo mã liên kết
                </Button>
              </div>
            </>
          )}
        </div>

        <div>
          <h3 className="mb-2">Công cụ đã liên kết</h3>
          {loading ? (
            <p className="text-muted-foreground" role="status">Đang tải danh sách</p>
          ) : links.length === 0 ? (
            <EmptyState title="Chưa liên kết công cụ nào">
              Bấm &ldquo;Tạo mã liên kết&rdquo; ở trên, rồi dán mã vào công cụ của bạn.
            </EmptyState>
          ) : (
            <ul className="divide-y divide-border border-y border-border">
              {links.map((l) => (
                <li key={l.tool_user_id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="break-words font-medium">{l.label || l.tool_user_id}</p>
                    <p className="text-sm text-muted-foreground">
                      Trạng thái: {l.status} · liên kết lúc {l.linked_at ? new Date(l.linked_at).toLocaleString("vi-VN") : "chưa rõ"}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    onClick={() => handleRevoke(l)}
                    disabled={revokingId === l.tool_user_id}
                    className="text-destructive"
                    aria-label={`Gỡ liên kết ${l.label || l.tool_user_id}`}
                  >
                    <Trash2 aria-hidden /> Gỡ liên kết
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Section>
  );
}
