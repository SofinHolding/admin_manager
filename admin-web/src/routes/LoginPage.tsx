import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Eye, EyeOff, Loader2, BarChart3 } from "lucide-react";
import { useAuth } from "../auth";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

export default function LoginPage() {
  const { login, user } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);

  // Toast thông báo từ redirect — phải nằm trong useEffect, không được gọi trong render
  // body vì React 19 sẽ lỗi "Cannot update a component while rendering a different component".
  // Và phải nằm TRƯỚC mọi early return — React yêu cầu hooks gọi cùng thứ tự mọi lần render.
  const toastShown = useRef(false);
  useEffect(() => {
    if (toastShown.current) return;
    if (params.get("verified") === "true") {
      toastShown.current = true;
      toast.success("Xác thực email thành công! Đăng nhập ngay.");
    } else if (params.get("reset") === "true") {
      toastShown.current = true;
      toast.success("Mật khẩu đã đặt lại. Đăng nhập với mật khẩu mới.");
    }
  }, [params]);

  // Đã đăng nhập → về dashboard. Dùng useEffect thay vì gọi navigate() trong render body
  // vì navigate() setState bên trong → "Cannot update while rendering another component".
  useEffect(() => {
    if (user) navigate("/dashboard", { replace: true });
  }, [user, navigate]);

  if (user) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setLoading(true);
    try {
      await login(username.trim(), password, remember);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-[400px]">
        {/* Logo */}
        <div className="mb-8 text-center">
          <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-xl bg-primary/15">
            <BarChart3 className="size-6 text-primary-text" />
          </div>
          <h1 className="text-xl font-semibold">Đăng nhập</h1>
          <p className="mt-1 text-sm text-muted-foreground">Xem thống kê và biểu đồ dữ liệu</p>
        </div>

        <form onSubmit={handleSubmit} className="glass rounded-xl p-6">
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="username">Tên đăng nhập</Label>
              <Input
                id="username"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Nhập tên đăng nhập"
                autoFocus
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="password">Mật khẩu</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPw ? "text" : "password"}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Nhập mật khẩu"
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
            </div>

            <div className="flex items-center justify-between">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                  className="rounded accent-primary"
                />
                Ghi nhớ đăng nhập
              </label>
              <Link to="/reset-password" className="text-sm text-primary-text hover:underline">
                Quên mật khẩu?
              </Link>
            </div>

            <Button type="submit" size="lg" className="w-full" disabled={loading || !username.trim() || !password}>
              {loading && <Loader2 className="size-4 animate-spin" />}
              Đăng nhập
            </Button>
          </div>
        </form>

        <div className="mt-4 text-center text-sm text-muted-foreground">
          Chưa có tài khoản?{" "}
          <Link to="/register" className="font-medium text-primary-text hover:underline">
            Đăng ký bằng mã mời
          </Link>
        </div>
      </div>
    </div>
  );
}
