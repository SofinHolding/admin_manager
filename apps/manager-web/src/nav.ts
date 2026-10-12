/**
 * Điều hướng theo VIỆC người dùng cần làm (ui-design-v1.md §2), ẩn/hiện theo quyền. Server vẫn là nơi cưỡng chế quyền.
 * Giữ nguyên đường dẫn (route) cũ; chỉ đổi cách gọi tên cho dễ hiểu.
 */

import type { LucideIcon } from "lucide-react";
import { BarChart3, Coins, Database, Globe, KeyRound, Link2, Settings, Users } from "lucide-react";
import { canSeeDashboard, CAP_MANUAL_CHANNEL_MGMT, CAP_TOOL_INTEGRATION, hasCap, PERM_REWARD_ACCESS, type PrincipalLike } from "./auth/permissions";

export interface NavItem {
  to: string;
  label: string;
  /** Tên ngắn cho thanh tab dưới (điện thoại), tránh bị cắt chữ. */
  short?: string;
  icon: LucideIcon;
}

export interface NavGroup {
  title: string;
  items: NavItem[];
}

const ROLE_LABEL: Record<string, string> = {
  admin: "Quản trị viên",
  dash_admin: "Quản lý Dashboard",
  viewer: "Người xem",
  discord: "Vận hành Discord",
};

export function roleLabel(role: string | undefined): string {
  return (role && ROLE_LABEL[role]) || "Thành viên";
}

export function navFor(user: PrincipalLike | null | undefined, hasPerm: (perm: string) => boolean): NavGroup[] {
  if (!user) return [];
  const isAdminZone = canSeeDashboard(user);
  const rewardBase = isAdminZone ? "/admin/reward" : "/reward";
  const groups: NavGroup[] = [];
  if (isAdminZone) {
    groups.push({
      title: "Theo dõi",
      items: [
        { to: "/admin/dashboard", label: "Tổng quan", icon: BarChart3 },
        { to: "/admin/groups", label: "Nhóm kênh", icon: Users },
      ],
    });
  }
  if (hasPerm(PERM_REWARD_ACCESS)) {
    groups.push({
      title: "Phân phối điểm",
      items: [
        { to: `${rewardBase}/credentials`, label: "Kết nối Discord", short: "Discord", icon: Link2 },
        { to: `${rewardBase}/jobs`, label: "Công việc phân phối", short: "Công việc", icon: Coins },
      ],
    });
  }
  if (user.role === "admin" || user.role === "dash_admin") {
    groups.push({ title: "Quản trị", items: [{ to: "/admin/manage", label: "Quản trị hệ thống", icon: Settings }] });
  }
  // Website-only: tài khoản không vào được khu quản trị nhưng có capability riêng (capability-model-v1.md §8).
  if (!isAdminZone && hasCap(user, CAP_TOOL_INTEGRATION)) {
    groups.push({ title: "Tool của tôi", items: [
      { to: "/my-tools",     label: "Tổng quan Tool",    icon: KeyRound },
      { to: "/integrations", label: "Khóa kết nối",  icon: Database },
    ] });
  }
  if (!isAdminZone && hasCap(user, CAP_MANUAL_CHANNEL_MGMT)) {
    groups.push({ title: "Kênh của tôi", items: [{ to: "/channels", label: "Kênh của tôi", icon: Globe }] });
  }
  return groups;
}
