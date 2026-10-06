import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { authApi } from "../api";
import { Button } from "../components/ui/button";
import { Notice } from "../components/kit";
import { AuthShell, Spinner } from "../components/authKit";

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
      setMessage("Liên kết xác thực không hợp lệ.");
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

  if (status === "loading") {
    return (
      <AuthShell title="Đang xác thực email" description="Vui lòng chờ trong giây lát.">
        <p role="status" className="flex items-center gap-2 text-muted-foreground">
          <Spinner /> Đang xác thực email của bạn
        </p>
      </AuthShell>
    );
  }

  if (status === "ok") {
    return (
      <AuthShell title="Đã xác thực email" description="Tài khoản của bạn đã sẵn sàng.">
        <div className="flex flex-col gap-5">
          <Notice tone="success" title="Xác thực thành công">{message || "Email của bạn đã được xác thực."}</Notice>
          <Button asChild size="lg" className="w-full">
            <Link to="/login?verified=true">Đăng nhập</Link>
          </Button>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Chưa xác thực được email" description="Liên kết này không dùng được.">
      <div className="flex flex-col gap-5">
        <Notice tone="error" title="Xác thực chưa thành công">
          {message}
          <span className="mt-1 block">
            Liên kết chỉ dùng được một lần và có thời hạn. Nếu bạn đã xác thực trước đó, hãy đăng nhập. Nếu chưa,
            hãy dùng liên kết trong thư mới nhất, hoặc báo quản trị viên.
          </span>
        </Notice>
        <Button asChild variant="outline" size="lg" className="w-full">
          <Link to="/login">Về trang đăng nhập</Link>
        </Button>
      </div>
    </AuthShell>
  );
}
