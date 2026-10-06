import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Eye, EyeOff, Loader2, BarChart3 } from "lucide-react";
import { authApi } from "../api";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

export default function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const navigate = useNavigate();

  // Nếu KHÔNG có token → hiện form "Quên mật khẩu" (nhập email)
  // Nếu CÓ token → hiện form "Đặt lại mật khẩu" (nhập password mới)

  if (token) return <NewPasswordForm token={token} />;
  return <ForgotPasswordForm />;
}

function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await authApi.forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-[400px]">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-xl bg-primary/15">
            <BarChart3 className="size-6 text-primary-text" />
          </div>
          <h1 className="text-xl font-semibold">Quên mật khẩu</h1>
        </div>

        {sent ? (
          <div className="glass rounded-xl p-6 text-center">
            <div className="mb-4 text-4xl">✉️</div>
            <p className="mb-4 text-sm text-muted-foreground">
              Nếu email tồn tại, đã gửi link đặt lại mật khẩu. Kiểm tra hộp thư.
            </p>
            <Link to="/login">
              <Button variant="outline" className="w-full">Về đăng nhập</Button>
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="glass rounded-xl p-6">
            <p className="mb-4 text-sm text-muted-foreground">
              Nhập email đã đăng ký để nhận link đặt lại mật khẩu.
            </p>
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="forgot-email">Email</Label>
                <Input
                  id="forgot-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="Nhập email đã đăng ký"
                  autoFocus
                />
              </div>
              <Button type="submit" className="w-full" disabled={loading || !email.includes("@")}>
                {loading && <Loader2 className="size-4 animate-spin" />}
                Gửi link đặt lại
              </Button>
              <Link to="/login" className="text-center text-sm text-primary-text hover:underline">
                Quay lại đăng nhập
              </Link>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function NewPasswordForm({ token }: { token: string }) {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);

  const pwOk = password.length >= 8;
  const match = confirm === password && confirm.length > 0;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pwOk || !match) return;
    setLoading(true);
    try {
      await authApi.resetPassword(token, password);
      toast.success("Mật khẩu đã đặt lại thành công!");
      navigate("/login?reset=true", { replace: true });
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-[400px]">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-xl bg-primary/15">
            <BarChart3 className="size-6 text-primary-text" />
          </div>
          <h1 className="text-xl font-semibold">Đặt lại mật khẩu</h1>
        </div>

        <form onSubmit={handleSubmit} className="glass rounded-xl p-6">
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="new-pw">Mật khẩu mới</Label>
              <div className="relative">
                <Input
                  id="new-pw"
                  type={showPw ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Tối thiểu 8 ký tự"
                  className="pr-10"
                  autoFocus
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
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="confirm-pw">Xác nhận mật khẩu mới</Label>
              <Input
                id="confirm-pw"
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="Nhập lại mật khẩu mới"
              />
              {confirm && !match && (
                <p className="text-xs text-destructive">Mật khẩu không khớp</p>
              )}
            </div>

            <Button type="submit" className="w-full" disabled={loading || !pwOk || !match}>
              {loading && <Loader2 className="size-4 animate-spin" />}
              Đặt lại mật khẩu
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
