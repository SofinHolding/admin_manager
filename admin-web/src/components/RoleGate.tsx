import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../auth";

/** Trang chủ theo vai trò: discord → UI phân phối điểm; admin/viewer → dashboard thống kê. */
export function roleHome(role: string | undefined): string {
  return role === "discord" ? "/reward/jobs" : "/dashboard";
}

/** Chặn route theo vai trò. Không đủ quyền → đá về trang chủ của chính vai trò đó (không phải 404). */
export function RoleGate({ allow }: { allow: string[] }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (!allow.includes(user.role)) return <Navigate to={roleHome(user.role)} replace />;
  return <Outlet />;
}

/** Điều hướng "/" và path lạ về đúng trang chủ theo vai trò người đang đăng nhập. */
export function HomeRedirect() {
  const { user } = useAuth();
  return <Navigate to={roleHome(user?.role)} replace />;
}
