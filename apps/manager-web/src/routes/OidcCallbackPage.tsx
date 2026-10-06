/**
 * Callback OIDC (`/admin/callback`): xác minh `state`, đổi `code` + PKCE verifier lấy token, vào ứng dụng.
 * State sai / thiếu giao dịch / provider báo lỗi -> hiện lỗi, KHÔNG nhận token.
 */

import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { Button } from "../components/ui/button";
import { Notice } from "../components/kit";
import { AuthShell, Spinner } from "../components/authKit";

export default function OidcCallbackPage() {
  const { completeOidcLogin } = useAuth();
  const navigate = useNavigate();
  const { search } = useLocation();
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    completeOidcLogin(search)
      .then((returnTo) => { if (alive) navigate(returnTo, { replace: true }); })
      .catch((e: unknown) => { if (alive) setError(e instanceof Error ? e.message : "Đăng nhập thất bại"); });
    return () => { alive = false; };
  }, [completeOidcLogin, navigate, search]);

  if (error) {
    return (
      <AuthShell title="Đăng nhập chưa hoàn tất" description="Có lỗi khi quay về từ trang đăng nhập chung của Manager.">
        <div className="flex flex-col gap-5">
          <Notice tone="error" title="Không hoàn tất được đăng nhập">
            <span className="block break-words">{error}</span>
            <span className="mt-1 block">Hãy thử đăng nhập lại. Nếu lỗi lặp lại, báo quản trị viên kèm nội dung trên.</span>
          </Notice>
          <Button asChild size="lg" className="w-full">
            <Link to="/login" replace>Quay lại đăng nhập</Link>
          </Button>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Đang đăng nhập" description="Vui lòng chờ, bạn sẽ được chuyển vào ứng dụng ngay.">
      <p role="status" className="flex items-center gap-2 text-muted-foreground">
        <Spinner /> Đang hoàn tất đăng nhập
      </p>
    </AuthShell>
  );
}
