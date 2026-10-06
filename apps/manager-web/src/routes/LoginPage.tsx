import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Field, Notice } from "../components/kit";
import { AuthShell, PasswordField, Spinner, TextLink, loginError, type FriendlyError } from "../components/authKit";

export default function LoginPage() {
  const { login, user, mode, loginWithOidc } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<FriendlyError | null>(null);
  const [missing, setMissing] = useState<{ username?: string; password?: string }>({});

  // Thông báo từ trang khác chuyển tới (?verified, ?reset): hiện ngay trong trang, không biến mất sau vài giây.
  const justVerified = params.get("verified") === "true";
  const justReset = params.get("reset") === "true";

  // Đã đăng nhập → về dashboard. Dùng useEffect thay vì gọi navigate() trong render body
  // vì navigate() setState bên trong → "Cannot update while rendering another component".
  useEffect(() => {
    if (user) navigate("/dashboard", { replace: true });
  }, [user, navigate]);

  if (user) return null;

  const footer = (
    <p>
      Chưa có tài khoản? <TextLink to="/register">Đăng ký bằng mã mời</TextLink>
    </p>
  );

  // Chế độ OIDC (VITE_AUTH_MODE=oidc): đăng nhập qua Identity bằng Authorization Code + PKCE.
  if (mode === "oidc") {
    const startSso = async () => {
      setLoading(true);
      setError(null);
      try {
        await loginWithOidc("/dashboard");
      } catch (err) {
        setError({
          title: "Chưa mở được trang đăng nhập",
          body: (err as Error).message || "Thử lại sau ít phút. Nếu vẫn lỗi, hãy báo quản trị viên.",
        });
        setLoading(false);
      }
    };
    return (
      <AuthShell title="Đăng nhập" description="Đăng nhập để xem thống kê kênh và quản lý nguồn dữ liệu của bạn." footer={footer}>
        <div className="flex flex-col gap-4">
          {error && <Notice tone="error" title={error.title}>{error.body}</Notice>}
          <p className="text-[0.9375rem] text-muted-foreground">
            Bạn sẽ được chuyển sang trang đăng nhập chung của Manager, rồi quay lại đây.
          </p>
          <Button size="lg" className="w-full" onClick={startSso} disabled={loading}>
            {loading && <Spinner />}
            Đăng nhập bằng tài khoản Manager
          </Button>
        </div>
      </AuthShell>
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const miss: { username?: string; password?: string } = {};
    if (!username.trim()) miss.username = "Nhập tên đăng nhập của bạn.";
    if (!password) miss.password = "Nhập mật khẩu.";
    setMissing(miss);
    if (miss.username || miss.password) return;
    setLoading(true);
    setError(null);
    try {
      await login(username.trim(), password, remember);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(loginError(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell title="Đăng nhập" description="Đăng nhập để xem thống kê kênh và quản lý nguồn dữ liệu của bạn." footer={footer}>
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-5">
        {justVerified && <Notice tone="success" title="Đã xác thực email">Đăng nhập để bắt đầu sử dụng.</Notice>}
        {justReset && <Notice tone="success" title="Đã đặt lại mật khẩu">Đăng nhập bằng mật khẩu mới.</Notice>}
        {error && <Notice tone="error" title={error.title}>{error.body}</Notice>}

        <Field label="Tên đăng nhập" error={missing.username}>
          {(p) => (
            <Input
              {...p}
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
            />
          )}
        </Field>

        <div>
          <PasswordField
            label="Mật khẩu"
            name="password"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
            error={missing.password}
          />
          <TextLink to="/reset-password" className="mt-1">Quên mật khẩu?</TextLink>
        </div>

        <div>
          <label className="flex min-h-11 cursor-pointer items-center gap-3 text-[0.9375rem] font-medium">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              aria-describedby="remember-hint"
              className="size-4 accent-primary"
            />
            Ghi nhớ đăng nhập
          </label>
          <p id="remember-hint" className="ml-8 text-sm text-muted-foreground">
            Chỉ giữ trong tab này. Đóng tab là bạn cần đăng nhập lại. Không nên chọn trên máy dùng chung.
          </p>
        </div>

        <Button type="submit" size="lg" className="w-full" disabled={loading}>
          {loading && <Spinner />}
          {loading ? "Đang đăng nhập" : "Đăng nhập"}
        </Button>
      </form>
    </AuthShell>
  );
}
