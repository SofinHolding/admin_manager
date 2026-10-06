import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Mail } from "lucide-react";
import { authApi } from "../api";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Field, Notice } from "../components/kit";
import {
  AuthShell, PASSWORD_RULE, PasswordField, Spinner, TextLink, formError, passwordLengthError,
  type FriendlyError,
} from "../components/authKit";

export default function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";

  // Nếu KHÔNG có token → hiện form "Quên mật khẩu" (nhập email)
  // Nếu CÓ token → hiện form "Đặt lại mật khẩu" (nhập password mới)

  if (token) return <NewPasswordForm token={token} />;
  return <ForgotPasswordForm />;
}

function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<FriendlyError | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.includes("@")) {
      setEmailError("Nhập địa chỉ email đầy đủ, ví dụ ten@congty.vn.");
      return;
    }
    setEmailError(null);
    setError(null);
    setLoading(true);
    try {
      await authApi.forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(formError(err, "Chưa gửi được liên kết"));
    } finally {
      setLoading(false);
    }
  };

  if (sent) {
    return (
      <AuthShell title="Kiểm tra email" description="Chúng tôi đã xử lý yêu cầu của bạn.">
        <div className="flex flex-col gap-5">
          <p className="flex items-start gap-3">
            <Mail className="mt-0.5 size-5 shrink-0 text-primary-text" aria-hidden />
            <span>
              Nếu email này có tài khoản, một liên kết đặt lại mật khẩu đã được gửi tới đó. Thư có thể mất vài phút
              để tới; nếu chưa thấy, hãy xem cả mục thư rác.
            </span>
          </p>
          <Button asChild variant="outline" size="lg" className="w-full">
            <Link to="/login">Về trang đăng nhập</Link>
          </Button>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Quên mật khẩu"
      description="Nhập email đã đăng ký, chúng tôi sẽ gửi liên kết để bạn đặt mật khẩu mới."
      footer={<p>Nhớ ra rồi? <TextLink to="/login">Quay lại đăng nhập</TextLink></p>}
    >
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-5">
        {error && <Notice tone="error" title={error.title}>{error.body}</Notice>}
        <Field label="Email" error={emailError}>
          {(p) => (
            <Input
              {...p}
              name="email"
              type="email"
              autoComplete="email"
              autoCapitalize="none"
              spellCheck={false}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoFocus
            />
          )}
        </Field>
        <Button type="submit" size="lg" className="w-full" disabled={loading}>
          {loading && <Spinner />}
          Gửi liên kết đặt lại
        </Button>
      </form>
    </AuthShell>
  );
}

function NewPasswordForm({ token }: { token: string }) {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<FriendlyError | null>(null);
  const [tried, setTried] = useState(false);

  const lengthError = passwordLengthError(password, tried);
  const match = confirm === password && confirm.length > 0;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTried(true);
    if (passwordLengthError(password, true) || !match) return;
    setLoading(true);
    setError(null);
    try {
      await authApi.resetPassword(token, password);
      navigate("/login?reset=true", { replace: true });
    } catch (err) {
      setError(formError(err, "Chưa đặt lại được mật khẩu"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell title="Đặt mật khẩu mới" description="Chọn mật khẩu mới cho tài khoản của bạn. Sau đó bạn đăng nhập lại.">
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-5">
        {error && (
          <Notice tone="error" title={error.title}>
            {error.body}
            {" "}Nếu liên kết đã hết hạn, hãy <Link to="/reset-password" className="font-semibold underline">yêu cầu liên kết mới</Link>.
          </Notice>
        )}
        <PasswordField
          label="Mật khẩu mới"
          noun="mật khẩu mới"
          name="new-password"
          autoComplete="new-password"
          value={password}
          onChange={setPassword}
          hint={PASSWORD_RULE}
          error={lengthError}
          autoFocus
        />
        <Field label="Nhập lại mật khẩu mới" error={confirm && !match ? "Hai mật khẩu chưa khớp. Nhập lại cho giống ô trên." : null}>
          {(p) => (
            <Input
              {...p}
              name="confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          )}
        </Field>
        <Button type="submit" size="lg" className="w-full" disabled={loading}>
          {loading && <Spinner />}
          Đặt mật khẩu mới
        </Button>
      </form>
    </AuthShell>
  );
}
