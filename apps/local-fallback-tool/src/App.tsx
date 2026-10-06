import { AuthProvider, useAuth } from "./auth";
import LoginPage from "./pages/LoginPage";
import InventoryPage from "./pages/InventoryPage";

function Shell() {
  const { user, loading, logout } = useAuth();

  if (loading) return <div className="p-6 text-sm text-gray-500">Đang tải…</div>;
  if (!user) return <LoginPage />;

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="flex h-12 items-center justify-between border-b border-gray-200 bg-white px-4">
        <div className="flex items-center gap-2">
          <span className="font-semibold">Local Fallback Tool</span>
          <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500">v1</span>
        </div>
        <div className="flex items-center gap-3 text-sm text-gray-600">
          <span>{user.username} ({user.role})</span>
          <button onClick={logout} className="rounded-md border border-gray-300 px-2.5 py-1 text-xs hover:bg-gray-100">
            Đăng xuất
          </button>
        </div>
      </header>
      <main className="p-4">
        <InventoryPage />
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
