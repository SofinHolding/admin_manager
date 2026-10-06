/**
 * Điều hướng theo VIỆC người dùng cần làm (ui-design-v1.md §2), ẩn/hiện theo quyền. Server vẫn là nơi cưỡng chế quyền.
 * Giữ nguyên đường dẫn (route) cũ; chỉ đổi cách gọi tên cho dễ hiểu.
 */

import type { LucideIcon } from "lucide-react";
import { BarChart3, Coins, Database, KeyRound, Link2, Settings, Users } from "lucide-react";
import { PERM_REWARD_ACCESS, type PrincipalLike } from "./auth/permissions";

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
  const dashboardRole = user.role === "admin" || user.role === "dash_admin" || user.role === "viewer";
  const groups: NavGroup[] = [];
  if (dashboardRole) {
    groups.push({
      title: "Theo dõi",
      items: [
        { to: "/dashboard", label: "Tổng quan", icon: BarChart3 },
        { to: "/groups", label: "Nhóm kênh", icon: Users },
      ],
    });
    groups.push({
      title: "Kết nối dữ liệu",
      items: [
        { to: "/resources", label: "Kênh và nguồn", short: "Kênh", icon: Database },
        { to: "/integrations", label: "Công cụ đẩy dữ liệu", short: "Công cụ", icon: KeyRound },
      ],
    });
  }
  if (hasPerm(PERM_REWARD_ACCESS)) {
    groups.push({
      title: "Phân phối điểm",
      items: [
        { to: "/reward/credentials", label: "Kết nối Discord", short: "Discord", icon: Link2 },
        { to: "/reward/jobs", label: "Công việc phân phối", short: "Công việc", icon: Coins },
      ],
    });
  }
  if (user.role === "admin" || user.role === "dash_admin") {
    groups.push({ title: "Quản trị", items: [{ to: "/manage", label: "Quản trị hệ thống", icon: Settings }] });
  }
  return groups;
}
