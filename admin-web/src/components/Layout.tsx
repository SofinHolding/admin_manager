import { NavLink, Outlet } from "react-router-dom";
import { BarChart3, Coins, Database, KeyRound, Link2, LogOut, Settings, User, Users } from "lucide-react";
import { useAuth } from "../auth";
import { Button } from "./ui/button";
import { PERM_REWARD_ACCESS } from "../auth/permissions";

export function Layout() {
  const { user, logout, hasPerm } = useAuth();
  const role = user?.role;
  const isDashRole = role === "admin" || role === "dash_admin";
  // Reward: theo permission `reward:access` (claim perms) — role admin được coi là có mọi quyền, role discord là shim legacy.
  const canReward = hasPerm(PERM_REWARD_ACCESS);

  const navLinkCls = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
      isActive
        ? "bg-primary/15 text-primary-text"
        : "text-muted-foreground hover:text-foreground"
    }`;

  return (
    <div className="flex min-h-screen flex-col">
      {/* Header */}
      <header className="glass sticky top-0 z-40 border-b border-hairline">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4">
          {/* Nav links — hiển thị theo vai trò, dùng chung 1 template */}
          <nav className="flex items-center gap-1">
            {(isDashRole || role === "viewer") && (
              <>
                <NavLink to="/dashboard" className={navLinkCls}>
                  <BarChart3 className="size-4" /> Thống kê
                </NavLink>
                <NavLink to="/groups" className={navLinkCls}>
                  <Users className="size-4" /> Nhóm kênh
                </NavLink>
                <NavLink to="/resources" className={navLinkCls}>
                  <Database className="size-4" /> Nguồn &amp; Resource
                </NavLink>
                <NavLink to="/integrations" className={navLinkCls}>
                  <KeyRound className="size-4" /> Tích hợp Tool
                </NavLink>
              </>
            )}
            {isDashRole && (
              <NavLink to="/manage" className={navLinkCls}>
                <Settings className="size-4" /> Quản trị
              </NavLink>
            )}
            {canReward && (
              <>
                <NavLink to="/reward/credentials" className={navLinkCls}>
                  <Link2 className="size-4" /> Kết nối Discord
                </NavLink>
                <NavLink to="/reward/jobs" className={navLinkCls}>
                  <Coins className="size-4" /> Phân phối điểm
                </NavLink>
              </>
            )}
          </nav>

          <div className="flex-1" />

          {user && (
            <div className="flex items-center gap-3">
              <NavLink
                to="/profile"
                className="flex items-center gap-2 rounded-md px-2 py-1 text-sm transition-colors hover:bg-elev-2"
                title="Hồ sơ cá nhân"
              >
                <div className="flex size-8 items-center justify-center rounded-full bg-primary/15 text-primary-text">
                  <User className="size-4" />
                </div>
                <span className="hidden sm:block">{user.username}</span>
              </NavLink>
              <Button variant="ghost" size="sm" onClick={logout} title="Đăng xuất">
                <LogOut className="size-4" />
                <span className="hidden sm:block">Đăng xuất</span>
              </Button>
            </div>
          )}
        </div>
      </header>

      {/* Content */}
      <main className="mx-auto w-full max-w-7xl flex-1 p-4">
        <Outlet />
      </main>
    </div>
  );
}
