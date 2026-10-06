/**
 * Callback OIDC (`/admin/callback`): xác minh `state`, đổi `code` + PKCE verifier lấy token, vào ứng dụng.
 * State sai / thiếu giao dịch / provider báo lỗi -> hiện lỗi, KHÔNG nhận token.
 */

import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { useAuth } from "../auth";

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

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="glass w-full max-w-[400px] rounded-xl p-6 text-center">
        {error ? (
          <>
            <h1 className="mb-2 text-lg font-semibold">Đăng nhập thất bại</h1>
            <p role="alert" className="mb-4 text-sm text-destructive">{error}</p>
            <Link to="/login" replace className="text-sm font-medium text-primary-text hover:underline">
              Quay lại đăng nhập
            </Link>
          </>
        ) : (
          <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Đang hoàn tất đăng nhập…
          </div>
        )}
      </div>
    </div>
  );
}
