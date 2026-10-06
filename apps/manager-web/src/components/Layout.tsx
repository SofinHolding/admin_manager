import * as React from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { LogOut, Menu, Monitor, Moon, Palette, Sun, User, X } from "lucide-react";
import { useAuth } from "../auth";
import { navFor, roleLabel, type NavGroup } from "../nav";
import {
  getTextSize, getThemePref, setTextSize, setThemePref, type TextSize, type ThemePref,
} from "../lib/preferences";
import { cn } from "../lib/utils";

const itemCls = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex min-h-10 items-center gap-3 rounded-md px-3 py-1.5 text-sm transition-colors",
    isActive
      ? "bg-primary/10 font-semibold text-primary-text shadow-[inset_3px_0_0_0_var(--color-primary)]"
      : "text-foreground/85 hover:bg-elev-2 hover:text-foreground",
  );

function NavList({ groups, onNavigate }: { groups: NavGroup[]; onNavigate?: () => void }) {
  return (
    <nav aria-label="Điều hướng chính" className="space-y-5">
      {groups.map((g) => (
        <div key={g.title}>
          <p className="mb-1 px-3 text-xs font-semibold text-muted-foreground">{g.title}</p>
          <ul className="space-y-0.5">
            {g.items.map((it) => (
              <li key={it.to}>
                <NavLink to={it.to} className={itemCls} onClick={onNavigate}>
                  <it.icon className="size-[1.125rem] shrink-0" aria-hidden />
                  <span>{it.label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

/** Nút chọn một trong nhiều: dùng cho giao diện sáng/tối và cỡ chữ. */
function Segmented<T extends string | number>({
  label, value, options, onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string; icon?: React.ReactNode; aria: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div role="group" aria-label={label}>
      <p className="mb-1 text-[0.8125rem] font-semibold text-muted-foreground">{label}</p>
      <div className="grid auto-cols-fr grid-flow-col gap-1 rounded-md bg-surface-2 p-1">
        {options.map((o) => (
          <button
            key={String(o.value)}
            type="button"
            aria-pressed={value === o.value}
            aria-label={o.aria}
            title={o.aria}
            onClick={() => onChange(o.value)}
            className={cn(
              "flex min-h-10 cursor-pointer items-center justify-center gap-1.5 rounded px-2 text-sm font-medium transition-colors",
              value === o.value ? "bg-surface-1 text-foreground shadow-sm ring-1 ring-border-strong" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {o.icon}
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function DisplayPrefs() {
  const [theme, setTheme] = React.useState<ThemePref>(getThemePref);
  const [size, setSize] = React.useState<TextSize>(getTextSize);
  return (
    <details className="group rounded-md">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-3 rounded-md px-3 text-[0.9375rem] text-foreground/85 hover:bg-elev-2 hover:text-foreground">
        <Palette className="size-5" aria-hidden />
        Giao diện và cỡ chữ
      </summary>
      <div className="mt-2 space-y-3 px-1 pb-1">
      <Segmented<ThemePref>
        label="Giao diện"
        value={theme}
        onChange={(v) => { setTheme(v); setThemePref(v); }}
        options={[
          { value: "system", label: "Máy", icon: <Monitor className="size-4" aria-hidden />, aria: "Giao diện theo máy" },
          { value: "light", label: "Sáng", icon: <Sun className="size-4" aria-hidden />, aria: "Giao diện sáng" },
          { value: "dark", label: "Tối", icon: <Moon className="size-4" aria-hidden />, aria: "Giao diện tối" },
        ]}
      />
      <Segmented<TextSize>
        label="Cỡ chữ"
        value={size}
        onChange={(v) => { setSize(v); setTextSize(v); }}
        options={[
          { value: 100, label: "A", aria: "Chữ chuẩn" },
          { value: 112, label: "A+", aria: "Chữ lớn" },
          { value: 125, label: "A++", aria: "Chữ rất lớn" },
        ]}
      />
      </div>
    </details>
  );
}

function AccountBlock({ onNavigate }: { onNavigate?: () => void }) {
  const { user, logout } = useAuth();
  if (!user) return null;
  return (
    <div className="space-y-3 border-t border-border pt-4">
      <DisplayPrefs />
      <NavLink to="/profile" onClick={onNavigate} className={({ isActive }) => cn(itemCls({ isActive }), "gap-3")} title="Hồ sơ cá nhân">
        <span className="grid size-9 shrink-0 place-items-center rounded-full bg-primary/12 font-semibold text-primary-text" aria-hidden>
          {(user.username[0] ?? "?").toUpperCase()}
        </span>
        <span className="min-w-0">
          <span className="block truncate font-semibold">{user.username}</span>
          <span className="block truncate text-[0.8125rem] text-muted-foreground">{roleLabel(user.role)}</span>
        </span>
      </NavLink>
      <button
        type="button"
        onClick={logout}
        className="flex min-h-11 w-full cursor-pointer items-center gap-3 rounded-md px-3 text-[0.9375rem] text-foreground/85 transition-colors hover:bg-elev-2 hover:text-foreground"
      >
        <LogOut className="size-5" aria-hidden />
        Đăng xuất
      </button>
    </div>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-3">
      <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground" aria-hidden>
        <span className="size-3 rounded-full bg-accent-2" />
      </span>
      <span className="text-base font-bold tracking-tight">Manager</span>
    </div>
  );
}

export function Layout() {
  const { user, hasPerm } = useAuth();
  const groups = React.useMemo(() => navFor(user, hasPerm), [user, hasPerm]);
  const [drawer, setDrawer] = React.useState(false);
  const location = useLocation();
  const main = React.useRef<HTMLElement>(null);

  // Chuyển trang: đưa focus về vùng nội dung (người dùng bàn phím/đọc màn hình không bị kẹt ở menu)
  React.useEffect(() => {
    main.current?.focus({ preventScroll: true });
  }, [location.pathname]);

  const quick = groups.flatMap((g) => g.items).slice(0, 4);

  return (
    <div className="min-h-screen lg:pl-64">
      <a href="#main" className="skip-link">Bỏ qua điều hướng</a>

      {/* Thanh bên — máy tính */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col gap-6 overflow-y-auto border-r border-border bg-surface-1 px-3 py-5 lg:flex">
        <Brand />
        <div className="flex-1"><NavList groups={groups} /></div>
        <AccountBlock />
      </aside>

      {/* Thanh trên — điện thoại/máy tính bảng */}
      <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-border bg-surface-1 px-3 lg:hidden">
        <Brand />
        <button
          type="button"
          onClick={() => setDrawer(true)}
          aria-label="Mở menu"
          className="grid size-11 cursor-pointer place-items-center rounded-md hover:bg-elev-2"
        >
          <Menu className="size-6" aria-hidden />
        </button>
      </header>

      <DialogPrimitive.Root open={drawer} onOpenChange={setDrawer}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-[oklch(0.15_0.03_215/55%)] lg:hidden" />
          <DialogPrimitive.Content
            aria-describedby={undefined}
            className="fixed inset-y-0 right-0 z-50 flex w-[min(20rem,88vw)] flex-col gap-5 overflow-y-auto border-l border-border-strong bg-surface-1 px-3 py-4 shadow-[var(--shadow-overlay)] lg:hidden"
          >
            <div className="flex items-center justify-between">
              <DialogPrimitive.Title className="px-3 text-lg font-bold">Menu</DialogPrimitive.Title>
              <DialogPrimitive.Close className="grid size-11 cursor-pointer place-items-center rounded-md hover:bg-elev-2" aria-label="Đóng menu">
                <X className="size-5" aria-hidden />
              </DialogPrimitive.Close>
            </div>
            <div className="flex-1"><NavList groups={groups} onNavigate={() => setDrawer(false)} /></div>
            <AccountBlock onNavigate={() => setDrawer(false)} />
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>

      <main id="main" ref={main} tabIndex={-1} className="mx-auto w-full max-w-6xl px-4 pb-28 pt-6 outline-none md:px-8 md:pt-8 lg:pb-12">
        <Outlet />
      </main>

      {/* Thanh tab dưới — điện thoại */}
      {quick.length > 0 && (
        <nav aria-label="Lối tắt" className="fixed inset-x-0 bottom-0 z-30 grid grid-flow-col auto-cols-fr border-t border-border bg-surface-1 pb-[env(safe-area-inset-bottom)] lg:hidden">
          {quick.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
              className={({ isActive }) => cn(
                "flex min-h-14 flex-col items-center justify-center gap-0.5 px-1 text-[0.75rem] font-medium",
                isActive ? "text-primary-text" : "text-muted-foreground",
              )}
            >
              <it.icon className="size-5" aria-hidden />
              <span className="max-w-full truncate">{it.short ?? it.label}</span>
            </NavLink>
          ))}
          <button type="button" onClick={() => setDrawer(true)} className="flex min-h-14 cursor-pointer flex-col items-center justify-center gap-0.5 px-1 text-[0.75rem] font-medium text-muted-foreground">
            <User className="size-5" aria-hidden />
            Thêm
          </button>
        </nav>
      )}
    </div>
  );
}
