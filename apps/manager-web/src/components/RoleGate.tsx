import { Link, Navigate, Outlet } from "react-router-dom";
import { ShieldAlert } from "lucide-react";
import { useAuth } from "../auth";
import { homePath } from "../auth/permissions";
import { roleLabel } from "../nav";
import { Button } from "./ui/button";
import { EmptyState, PageHeader, TechDetails } from "./kit";
import { roleAbout } from "./authKit";

/** Trang chủ theo quyền: chỉ có `reward:access` (vd role discord) -> phân phối điểm; còn lại -> dashboard. */
export function roleHome(user: { role: string; perms?: string[] } | null | undefined): string {
  return homePath(user);
}

const PERM_LABEL: Record<string, string> = {
  "reward:access": "truy cập phân phối điểm",
  "reward:admin": "quản trị phân phối điểm",
};

/**
 * Trang "không đủ quyền": nói bạn đang là ai, vì sao không vào được và đưa về trang chính.
 * (Server vẫn là nơi cưỡng chế; đây chỉ là cách giải thích cho người dùng.)
 */
export function AccessDenied({ role, requirement, perm }: { role: string; requirement: string; perm?: string }) {
  const { user } = useAuth();
  const home = roleHome(user);
  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title="Bạn chưa có quyền vào trang này" description="Tài khoản của bạn không được cấp quyền cho trang này, nên chúng tôi không mở nó." />
      <EmptyState icon={ShieldAlert} title={`Bạn đang đăng nhập với vai trò ${roleLabel(role)}`}
        action={<Button asChild size="lg"><Link to={home}>Về trang chính</Link></Button>}>
        <span className="block">{requirement}</span>
        <span className="mt-1 block">Vai trò của bạn làm được: {roleAbout(role).replace(/\.$/, "")}.</span>
        <span className="mt-1 block">Cần dùng trang này? Hãy nhờ quản trị viên cấp quyền cho bạn.</span>
      </EmptyState>
      {perm && (
        <TechDetails className="mt-4">
          <p>Quyền cần có: <code className="font-mono">{perm}</code></p>
        </TechDetails>
      )}
    </div>
  );
}

/** Chặn route theo vai trò. Không đủ quyền → hiện trang giải thích (không phải 404, không chuyển trang âm thầm). */
export function RoleGate({ allow }: { allow: string[] }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (!allow.includes(user.role)) {
    return <AccessDenied role={user.role} requirement={`Trang này chỉ dành cho: ${allow.map(roleLabel).join(", ")}.`} />;
  }
  return <Outlet />;
}

/** Chặn route theo permission (claim `perms`; role admin được coi là có mọi quyền). */
export function PermGate({ perm }: { perm: string }) {
  const { user, hasPerm } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (!hasPerm(perm)) {
    return <AccessDenied role={user.role} perm={perm} requirement={`Trang này cần quyền ${PERM_LABEL[perm] ?? perm}, mà tài khoản của bạn chưa được cấp.`} />;
  }
  return <Outlet />;
}

/** Điều hướng "/" và path lạ về đúng trang chủ theo người đang đăng nhập. */
export function HomeRedirect() {
  const { user } = useAuth();
  return <Navigate to={roleHome(user)} replace />;
}
