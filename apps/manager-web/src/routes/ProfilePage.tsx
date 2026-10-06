/**
 * Trang hồ sơ cá nhân — user tự đổi mật khẩu và cập nhật thông tin.
 *
 * Đổi mật khẩu cần xác thực mã 6 số gửi qua email TRƯỚC khi cho nhập mật khẩu mới.
 * Luồng: Gửi mã → Nhập mã + mật khẩu mới → Đổi thành công → Đăng nhập lại.
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Copy, KeyRound, Link2, Mail, RefreshCw, Save, Send, Trash2, User } from "lucide-react";
import { useAuth } from "../auth";
import { linksApi, profileApi, type ViewerLink } from "../api";
import { Button } from "../components/ui/button";

export default function ProfilePage() {
  const { user } = useAuth();

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <h1 className="text-xl font-bold">Hồ sơ cá nhân</h1>
      {user && <ProfileForm user={user} />}
      <ToolLinksCard />
      <ChangePasswordForm />
    </div>
  );
}

// ── Cập nhật hồ sơ ──────────────────────────────────────────────────────────

function ProfileForm({ user }: { user: { username: string; email: string; role: string } }) {
  const [username, setUsername] = useState(user.username);
  const [email, setEmail] = useState(user.email);
  const [saving, setSaving] = useState(false);

  const hasChanges = username !== user.username || email !== user.email;

  const handleSave = async () => {
    if (!hasChanges) return;
    setSaving(true);
    try {
      const data: Record<string, string> = {};
      if (username !== user.username) data.username = username;
      if (email !== user.email) data.email = email;
      await profileApi.updateProfile(data);
      toast.success("Đã cập nhật hồ sơ. Tải lại trang để thấy thay đổi.");
    } catch (e: any) {
      toast.error(e.message || "Cập nhật thất bại");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-xl border border-border bg-elev-1 p-5">
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold">
        <User className="size-4" /> Thông tin tài khoản
      </h3>

      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Tên đăng nhập</label>
          <input
            type="text"
            className="h-9 w-full rounded-md border border-border bg-elev-1 px-3 text-sm outline-none transition-colors focus:border-primary"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            minLength={3}
            maxLength={30}
          />
        </div>

        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Email</label>
          <div className="flex items-center gap-2">
            <Mail className="size-4 text-muted-foreground" />
            <input
              type="email"
              className="h-9 flex-1 rounded-md border border-border bg-elev-1 px-3 text-sm outline-none transition-colors focus:border-primary"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
        </div>

        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Vai trò</label>
          <span className="inline-block rounded-full bg-primary/10 px-3 py-1 text-xs font-medium capitalize text-primary-text">
            {user.role}
          </span>
        </div>

        {hasChanges && (
          <Button onClick={handleSave} disabled={saving} className="mt-2 gap-1.5">
            {saving ? <RefreshCw className="size-4 animate-spin" /> : <Save className="size-4" />}
            Lưu thay đổi
          </Button>
        )}
      </div>
    </div>
  );
}

// ── Liên kết Tool ───────────────────────────────────────────────────────────
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

  const load = async () => {
    setLoading(true);
    try {
      const { items } = await linksApi.list();
      setLinks(items);
    } catch (e) {
      toast.error(errMsg(e, "Không tải được danh sách liên kết"));
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
    try {
      const res = await linksApi.createCode();
      setCode(res.code);
      setExpiresAt(res.expires_at);
      setNow(Date.now());
      toast.success("Đã tạo mã liên kết. Mã hết hạn sau 15 phút.");
    } catch (e) {
      toast.error(errMsg(e, "Tạo mã thất bại"));
    } finally {
      setCreating(false);
    }
  };

  const handleCopy = () => {
    if (!code) return;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(code).then(() => toast.success("Đã copy mã")).catch(() => {});
    }
  };

  const handleRevoke = async (link: ViewerLink) => {
    if (!confirm(`Gỡ liên kết Tool "${link.label || link.tool_user_id}"?`)) return;
    setRevokingId(link.tool_user_id);
    try {
      await linksApi.revoke(link.tool_user_id);
      setLinks((prev) => prev.filter((l) => l.tool_user_id !== link.tool_user_id));
      toast.success("Đã gỡ liên kết");
    } catch (e) {
      toast.error(errMsg(e, "Gỡ liên kết thất bại"));
    } finally {
      setRevokingId(null);
    }
  };

  return (
    <div className="rounded-xl border border-border bg-elev-1 p-5">
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold">
        <Link2 className="size-4" /> Liên kết Tool
      </h3>

      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Tạo mã và dán vào Tool để liên kết tài khoản của bạn với dữ liệu thu thập.
        </p>

        {code && remainingMs > 0 ? (
          <div className="flex items-center gap-3 rounded-lg border border-primary/30 bg-primary/5 px-4 py-3">
            <span className="flex-1 font-mono text-2xl font-bold tracking-widest text-primary-text">{code}</span>
            <Button variant="ghost" size="sm" onClick={handleCopy} title="Copy mã">
              <Copy className="size-4" />
            </Button>
            <span className="text-xs text-muted-foreground">{fmtCountdown(remainingMs)}</span>
          </div>
        ) : (
          <Button onClick={handleCreate} disabled={creating} className="gap-1.5">
            {creating ? <RefreshCw className="size-4 animate-spin" /> : <Link2 className="size-4" />}
            Tạo mã liên kết Tool
          </Button>
        )}
        {code && remainingMs <= 0 && (
          <p className="text-xs text-red-500">Mã đã hết hạn — tạo mã mới ở trên.</p>
        )}

        <div className="pt-2">
          <h4 className="mb-2 text-xs font-semibold text-muted-foreground">Tool đã liên kết</h4>
          {loading ? (
            <p className="text-sm text-muted-foreground">Đang tải…</p>
          ) : links.length === 0 ? (
            <p className="text-sm text-muted-foreground">Chưa liên kết Tool nào.</p>
          ) : (
            <ul className="space-y-2">
              {links.map((l) => (
                <li
                  key={l.tool_user_id}
                  className="flex items-center justify-between rounded-md border border-border/60 px-3 py-2 text-sm"
                >
                  <div>
                    <div className="font-medium">{l.label || l.tool_user_id}</div>
                    <div className="text-xs text-muted-foreground">
                      {l.status} · liên kết lúc {l.linked_at ? new Date(l.linked_at).toLocaleString("vi-VN") : "—"}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleRevoke(l)}
                    disabled={revokingId === l.tool_user_id}
                    className="text-red-500 hover:text-red-600"
                    title="Gỡ liên kết"
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Đổi mật khẩu ────────────────────────────────────────────────────────────

function ChangePasswordForm() {
  const [step, setStep] = useState<"idle" | "code_sent" | "done">("idle");
  const [sending, setSending] = useState(false);
  const [changing, setChanging] = useState(false);
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const handleSendCode = async () => {
    setSending(true);
    try {
      await profileApi.sendChangeCode();
      setStep("code_sent");
      toast.success("Đã gửi mã xác thực về email của bạn.");
    } catch (e: any) {
      toast.error(e.message || "Gửi mã thất bại");
    } finally {
      setSending(false);
    }
  };

  const handleChange = async () => {
    if (newPassword !== confirmPassword) {
      toast.error("Mật khẩu xác nhận không khớp");
      return;
    }
    if (newPassword.length < 8) {
      toast.error("Mật khẩu phải có ít nhất 8 ký tự");
      return;
    }

    setChanging(true);
    try {
      await profileApi.changePassword(code, newPassword);
      setStep("done");
      toast.success("Mật khẩu đã thay đổi. Bạn sẽ cần đăng nhập lại.");
    } catch (e: any) {
      toast.error(e.message || "Đổi mật khẩu thất bại");
    } finally {
      setChanging(false);
    }
  };

  return (
    <div className="rounded-xl border border-border bg-elev-1 p-5">
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold">
        <KeyRound className="size-4" /> Đổi mật khẩu
      </h3>

      {step === "idle" && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Để bảo mật, hệ thống sẽ gửi mã xác thực 6 số về email của bạn trước khi cho phép đổi mật khẩu.
          </p>
          <Button onClick={handleSendCode} disabled={sending} className="gap-1.5">
            {sending ? <RefreshCw className="size-4 animate-spin" /> : <Send className="size-4" />}
            Gửi mã xác thực
          </Button>
        </div>
      )}

      {step === "code_sent" && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Nhập mã 6 số đã gửi về email (mã hết hạn sau 10 phút).
          </p>

          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Mã xác thực</label>
            <input
              type="text"
              inputMode="numeric"
              className="h-10 w-40 rounded-md border border-border bg-elev-1 px-3 text-center font-mono text-lg tracking-widest outline-none transition-colors focus:border-primary"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              placeholder="000000"
              autoFocus
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Mật khẩu mới</label>
            <input
              type="password"
              className="h-9 w-full rounded-md border border-border bg-elev-1 px-3 text-sm outline-none transition-colors focus:border-primary"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="Tối thiểu 8 ký tự"
              minLength={8}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Xác nhận mật khẩu mới</label>
            <input
              type="password"
              className="h-9 w-full rounded-md border border-border bg-elev-1 px-3 text-sm outline-none transition-colors focus:border-primary"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Nhập lại mật khẩu mới"
            />
            {confirmPassword && confirmPassword !== newPassword && (
              <p className="mt-1 text-xs text-red-500">Mật khẩu xác nhận không khớp</p>
            )}
          </div>

          <div className="flex items-center gap-2">
            <Button
              onClick={handleChange}
              disabled={changing || code.length !== 6 || newPassword.length < 8 || newPassword !== confirmPassword}
              className="gap-1.5"
            >
              {changing ? <RefreshCw className="size-4 animate-spin" /> : <KeyRound className="size-4" />}
              Đổi mật khẩu
            </Button>
            <Button variant="ghost" size="sm" onClick={handleSendCode} disabled={sending}>
              Gửi lại mã
            </Button>
          </div>
        </div>
      )}

      {step === "done" && (
        <div className="space-y-2">
          <p className="text-sm text-green-600">
            ✅ Mật khẩu đã thay đổi thành công. Phiên đăng nhập sẽ hết hạn, bạn cần đăng nhập lại bằng mật khẩu mới.
          </p>
        </div>
      )}
    </div>
  );
}
