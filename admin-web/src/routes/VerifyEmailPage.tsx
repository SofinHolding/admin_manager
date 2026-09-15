import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Check, X, Loader2 } from "lucide-react";
import { authApi } from "../api";
import { Button } from "../components/ui/button";

export default function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [message, setMessage] = useState("");
  // React 19 strict mode gọi useEffect 2 lần trong dev. Lần 1 verify xoá token ở server, lần 2
  // gửi lại token rỗng → 400. Ref chặn lần gọi thứ hai ghi đè kết quả thành công.
  const called = useRef(false);

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setMessage("Link xác thực không hợp lệ.");
      return;
    }
    if (called.current) return;
    called.current = true;
    // Xóa token khỏi URL ngay lập tức — không để lộ trong lịch sử trình duyệt
    window.history.replaceState({}, "", window.location.pathname);
    authApi.verifyEmail(token)
      .then((d) => { setStatus("ok"); setMessage(d.message); })
      .catch((e) => { setStatus("error"); setMessage((e as Error).message); });
  }, [token]);

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="glass w-full max-w-sm rounded-xl p-8 text-center">
        {status === "loading" && (
          <>
            <Loader2 className="mx-auto mb-4 size-8 animate-spin text-primary-text" />
            <p className="text-sm text-muted-foreground">Đang xác thực...</p>
          </>
        )}
        {status === "ok" && (
          <>
            <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-full bg-success/15">
              <Check className="size-6 text-success" />
            </div>
            <h2 className="mb-2 font-semibold">Xác thực thành công!</h2>
            <p className="mb-6 text-sm text-muted-foreground">{message}</p>
            <Link to="/login?verified=true">
              <Button className="w-full" size="lg">Đăng nhập ngay</Button>
            </Link>
          </>
        )}
        {status === "error" && (
          <>
            <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-full bg-destructive/15">
              <X className="size-6 text-destructive" />
            </div>
            <h2 className="mb-2 font-semibold">Xác thực thất bại</h2>
            <p className="mb-6 text-sm text-muted-foreground">{message}</p>
            <Link to="/login">
              <Button variant="outline" className="w-full">Về trang đăng nhập</Button>
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
