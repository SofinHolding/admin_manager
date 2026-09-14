import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, ArrowRight, BarChart3, Check, Eye, EyeOff, Loader2, X } from "lucide-react";
import { authApi } from "../api";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

type Step = 1 | 2 | 3;

export default function RegisterPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>(1);

  // Bước 1
  const [inviteKey, setInviteKey] = useState("");
  const [keyRole, setKeyRole] = useState("");
  const [keyLoading, setKeyLoading] = useState(false);

  // Bước 2
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [regLoading, setRegLoading] = useState(false);

  // Validation debounce
  const [usernameOk, setUsernameOk] = useState<boolean | null>(null);
  const [emailOk, setEmailOk] = useState<boolean | null>(null);
  const usernameTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const emailTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Bước 3
  const [registeredEmail, setRegisteredEmail] = useState("");

  // ── Bước 1: validate key ────────────────────────────────────────────────

  const validateKey = async () => {
    if (!inviteKey.trim()) return;
    setKeyLoading(true);
    try {
      const d = await authApi.validateKey(inviteKey.trim());
      if (d.valid) {
        setKeyRole(d.role || "viewer");
        setStep(2);
      } else {
        toast.error(d.reason || "Mã mời không hợp lệ");
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setKeyLoading(false);
    }
  };

  // ── Bước 2: debounce check username/email ───────────────────────────────

  const checkUsername = useCallback((v: string) => {
    clearTimeout(usernameTimer.current);
    if (v.length < 3) { setUsernameOk(null); return; }
    usernameTimer.current = setTimeout(async () => {
      try {
        const d = await authApi.checkUsername(v);
        setUsernameOk(d.available);
      } catch { setUsernameOk(null); }
    }, 500);
  }, []);

  const checkEmail = useCallback((v: string) => {
    clearTimeout(emailTimer.current);
    if (!v.includes("@")) { setEmailOk(null); return; }
    emailTimer.current = setTimeout(async () => {
      try {
        const d = await authApi.checkEmail(v);
        setEmailOk(d.available);
      } catch { setEmailOk(null); }
    }, 500);
  }, []);

  useEffect(() => { checkUsername(username); }, [username, checkUsername]);
  useEffect(() => { checkEmail(email); }, [email, checkEmail]);

  const pwOk = password.length >= 8;
  const confirmOk = confirmPw === password && confirmPw.length > 0;
  const canRegister = username.length >= 3 && usernameOk && email.includes("@") && emailOk && pwOk && confirmOk;

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canRegister) return;
    setRegLoading(true);
    try {
      const d = await authApi.register(inviteKey.trim(), username.trim(), password, email.trim());
      setRegisteredEmail(d.email);
      setStep(3);
      toast.success("Đăng ký thành công!");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setRegLoading(false);
    }
  };

  // ── Bước 3: resend verify ──────────────────────────────────────────────

  const [resendCooldown, setResendCooldown] = useState(0);
  const resendTimer = useRef<ReturnType<typeof setInterval>>(undefined);

  const handleResend = async () => {
    if (resendCooldown > 0) return;
    try {
      await authApi.resendVerify(registeredEmail);
      toast.success("Đã gửi lại email xác thực");
      setResendCooldown(60);
      resendTimer.current = setInterval(() => {
        setResendCooldown((s) => {
          if (s <= 1) { clearInterval(resendTimer.current); return 0; }
          return s - 1;
        });
      }, 1000);
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  // ── Render ──────────────────────────────────────────────────────────────

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-[400px]">
        {/* Logo */}
        <div className="mb-8 text-center">
          <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-xl bg-primary/15">
            <BarChart3 className="size-6 text-primary-text" />
          </div>
          <h1 className="text-xl font-semibold">Đăng ký tài khoản</h1>
          <p className="mt-1 text-sm text-muted-foreground">Bước {step}/3</p>
        </div>

        {/* ── Bước 1: Invite key ───────────────────────────────────────── */}
        {step === 1 && (
          <div className="glass rounded-xl p-6">
            <h2 className="mb-1 font-semibold">Nhập mã mời</h2>
            <p className="mb-4 text-sm text-muted-foreground">
              Paste mã mời bạn nhận được từ quản trị viên
            </p>
            <div className="flex flex-col gap-4">
              <Input
                value={inviteKey}
                onChange={(e) => setInviteKey(e.target.value)}
                placeholder="Mã mời"
                autoFocus
                onKeyDown={(e) => e.key === "Enter" && validateKey()}
              />
              <Button onClick={validateKey} disabled={keyLoading || !inviteKey.trim()} className="w-full">
                {keyLoading && <Loader2 className="size-4 animate-spin" />}
                Tiếp tục <ArrowRight className="size-4" />
              </Button>
            </div>
            <div className="mt-4 text-center text-sm text-muted-foreground">
              <Link to="/login" className="text-primary-text hover:underline">
                <ArrowLeft className="mr-1 inline size-3" />
                Quay lại đăng nhập
              </Link>
            </div>
          </div>
        )}

        {/* ── Bước 2: Form đăng ký ────────────────────────────────────── */}
        {step === 2 && (
          <form onSubmit={handleRegister} className="glass rounded-xl p-6">
            <h2 className="mb-1 font-semibold">Tạo tài khoản</h2>
            <p className="mb-4 text-sm text-muted-foreground">Điền thông tin để tạo tài khoản mới</p>
            <div className="flex flex-col gap-4">
              {/* Username */}
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="reg-username">Tên đăng nhập</Label>
                <div className="relative">
                  <Input
                    id="reg-username"
                    value={username}
                    onChange={(e) => setUsername(e.target.value.replace(/[^a-zA-Z0-9_]/g, ""))}
                    placeholder="3-30 ký tự, chữ/số/gạch dưới"
                    maxLength={30}
                    autoFocus
                  />
                  {usernameOk !== null && (
                    <span className={`absolute right-2.5 top-1/2 -translate-y-1/2 ${usernameOk ? "text-success" : "text-destructive"}`}>
                      {usernameOk ? <Check className="size-4" /> : <X className="size-4" />}
                    </span>
                  )}
                </div>
                {usernameOk === false && (
                  <p className="text-xs text-destructive">Tên đã được sử dụng</p>
                )}
              </div>

              {/* Email */}
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="reg-email">Email</Label>
                <div className="relative">
                  <Input
                    id="reg-email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="Dùng để xác thực tài khoản"
                  />
                  {emailOk !== null && (
                    <span className={`absolute right-2.5 top-1/2 -translate-y-1/2 ${emailOk ? "text-success" : "text-destructive"}`}>
                      {emailOk ? <Check className="size-4" /> : <X className="size-4" />}
                    </span>
                  )}
                </div>
                {emailOk === false && (
                  <p className="text-xs text-destructive">Email đã được đăng ký</p>
                )}
              </div>

              {/* Password */}
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="reg-password">Mật khẩu</Label>
                <div className="relative">
                  <Input
                    id="reg-password"
                    type={showPw ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Tối thiểu 8 ký tự"
                    className="pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPw(!showPw)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    tabIndex={-1}
                  >
                    {showPw ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                  </button>
                </div>
                {password && (
                  <p className={`text-xs ${pwOk ? "text-success" : "text-muted-foreground"}`}>
                    {pwOk ? "✓ Đủ độ dài" : `Cần thêm ${8 - password.length} ký tự`}
                  </p>
                )}
              </div>

              {/* Confirm password */}
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="reg-confirm">Xác nhận mật khẩu</Label>
                <Input
                  id="reg-confirm"
                  type="password"
                  value={confirmPw}
                  onChange={(e) => setConfirmPw(e.target.value)}
                  placeholder="Nhập lại mật khẩu"
                />
                {confirmPw && !confirmOk && (
                  <p className="text-xs text-destructive">Mật khẩu không khớp</p>
                )}
              </div>

              <div className="flex gap-3">
                <Button type="button" variant="outline" onClick={() => setStep(1)} className="flex-1">
                  <ArrowLeft className="size-4" /> Quay lại
                </Button>
                <Button type="submit" disabled={regLoading || !canRegister} className="flex-1">
                  {regLoading && <Loader2 className="size-4 animate-spin" />}
                  Đăng ký <ArrowRight className="size-4" />
                </Button>
              </div>
            </div>
          </form>
        )}

        {/* ── Bước 3: Xác thực email ──────────────────────────────────── */}
        {step === 3 && (
          <div className="glass rounded-xl p-6 text-center">
            <div className="mb-4 text-4xl">✉️</div>
            <h2 className="mb-2 font-semibold">Xác thực email</h2>
            <p className="mb-1 text-sm text-muted-foreground">Đã gửi email xác thực tới</p>
            <p className="mb-4 font-medium text-primary-text">{registeredEmail}</p>
            <p className="mb-6 text-sm text-muted-foreground">
              Kiểm tra hộp thư và click vào link xác thực để kích hoạt tài khoản.
            </p>
            <div className="flex flex-col gap-3">
              <Button variant="outline" onClick={handleResend} disabled={resendCooldown > 0} className="w-full">
                {resendCooldown > 0 ? `Gửi lại sau ${resendCooldown}s` : "Gửi lại email"}
              </Button>
              <Link to="/login">
                <Button variant="ghost" className="w-full">
                  <ArrowLeft className="size-4" /> Đăng nhập bằng tài khoản khác
                </Button>
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
