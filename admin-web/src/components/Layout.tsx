import { NavLink, Outlet } from "react-router-dom";
import { BarChart3, Coins, Link2, LogOut, Settings, User } from "lucide-react";
import { useAuth } from "../auth";
import { Button } from "./ui/button";

export function Layout() {
  const { user, logout } = useAuth();
  const role = user?.role;
  const isAdmin = role === "admin";
  const isDiscord = role === "discord";

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
            {(isAdmin || role === "viewer") && (
              <NavLink to="/dashboard" className={navLinkCls}>
                <BarChart3 className="size-4" /> Thống kê
              </NavLink>
            )}
            {isAdmin && (
              <NavLink to="/manage" className={navLinkCls}>
                <Settings className="size-4" /> Quản trị
              </NavLink>
            )}
            {(isDiscord || isAdmin) && (
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
