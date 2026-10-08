/**
 * Quyền ở phía UI — chỉ để ẩn/hiện điều hướng. Server (Identity/Reward/...) mới là nơi ENFORCE.
 *
 * Hợp đồng (docs/api/service-auth-contract-v1.md §2):
 *  - `role` thô: viewer | admin | dash_admin | discord (shim legacy).
 *  - `perms` (claim trong access token): quyền mịn theo domain, vd `reward:access`, `reward:admin`.
 *  - `role=admin` được coi là có mọi quyền quản trị; `dash_admin` KHÔNG tự có quyền domain.
 *  - Shim tương thích: role `discord` mặc định có `reward:access`.
 */

export interface PrincipalLike {
  role: string;
  perms?: string[];
}

export const PERM_REWARD_ACCESS = "reward:access";
export const PERM_REWARD_ADMIN = "reward:admin";

export function hasPerm(user: PrincipalLike | null | undefined, perm: string): boolean {
  if (!user) return false;
  if (user.role === "admin") return true;
  if (user.perms?.includes(perm)) return true;
  if (perm === PERM_REWARD_ACCESS && user.role === "discord") return true;
  return false;
}

export const DASHBOARD_ROLES = ["admin", "dash_admin", "viewer"] as const;

export function canSeeDashboard(user: PrincipalLike | null | undefined): boolean {
  return !!user && (DASHBOARD_ROLES as readonly string[]).includes(user.role);
}

/**
 * Capability — permission namespace `capability:*` (docs/architecture/capability-model-v1.md).
 * Tính năng tài khoản (có dùng Tool không, có được nhập kênh không) tách khỏi vai trò website.
 */
export const CAP_WEB_ACCESS = "capability:web_access";
export const CAP_DASHBOARD_ACCESS = "capability:dashboard_access";
export const CAP_MANUAL_CHANNEL_MGMT = "capability:manual_channel_mgmt";
export const CAP_TOOL_INTEGRATION = "capability:tool_integration";
export const CAP_API_WRITE = "capability:api_write";
export const CAP_API_READ = "capability:api_read";
export const CAP_MULTI_TOOL = "capability:multi_tool";

export function hasCap(user: PrincipalLike | null | undefined, cap: string): boolean {
  return (user?.perms ?? []).includes(cap);
}

/** Trang chủ: dashboard trước, rồi Tool cá nhân, rồi tự quản lý kênh, cuối cùng là hồ sơ. */
export function homePath(user: PrincipalLike | null | undefined): string {
  if (!user) return "/login";
  if (canSeeDashboard(user)) return "/admin/dashboard";
  if (!canSeeDashboard(user) && hasPerm(user, PERM_REWARD_ACCESS)) return "/reward/jobs";
  if (hasCap(user, CAP_TOOL_INTEGRATION)) return "/my-tools";
  if (hasCap(user, CAP_MANUAL_CHANNEL_MGMT)) return "/channels";
  return "/profile";
}
