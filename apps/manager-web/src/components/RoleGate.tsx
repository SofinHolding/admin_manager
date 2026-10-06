import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { homePath } from "../auth/permissions";

/** Trang chủ theo quyền: chỉ có `reward:access` (vd role discord) -> phân phối điểm; còn lại -> dashboard. */
export function roleHome(user: { role: string; perms?: string[] } | null | undefined): string {
  return homePath(user);
}

/** Chặn route theo vai trò. Không đủ quyền → đá về trang chủ của chính người đó (không phải 404). */
export function RoleGate({ allow }: { allow: string[] }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (!allow.includes(user.role)) return <Navigate to={roleHome(user)} replace />;
  return <Outlet />;
}

/** Chặn route theo permission (claim `perms`; role admin được coi là có mọi quyền). */
export function PermGate({ perm }: { perm: string }) {
  const { user, hasPerm } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (!hasPerm(perm)) return <Navigate to={roleHome(user)} replace />;
  return <Outlet />;
}

/** Điều hướng "/" và path lạ về đúng trang chủ theo người đang đăng nhập. */
export function HomeRedirect() {
  const { user } = useAuth();
  return <Navigate to={roleHome(user)} replace />;
}
