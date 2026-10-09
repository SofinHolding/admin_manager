import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { hasCap } from "../auth/permissions";
import { AccessDenied } from "./RoleGate";

const CAP_LABEL: Record<string, string> = {
  "capability:web_access":           "đăng nhập và dùng website",
  "capability:dashboard_access":     "xem Dashboard thống kê",
  "capability:manual_channel_mgmt":  "tự quản lý kênh",
  "capability:tool_integration":     "liên kết Tool cá nhân",
  "capability:api_write":            "gửi dữ liệu qua API",
  "capability:api_read":             "đọc dữ liệu qua API",
  "capability:multi_tool":           "sử dụng nhiều Tool",
};

/** Chặn route theo capability (claim `perms` namespace `capability:*`). Server mới là nơi ENFORCE. */
export function CapGate({ cap }: { cap: string }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (!hasCap(user, cap)) {
    return (
      <AccessDenied role={user.role} perm={cap}
        requirement={`Trang này cần quyền ${CAP_LABEL[cap] ?? cap}, mà tài khoản của bạn chưa được cấp.`} />
    );
  }
  return <Outlet />;
}
