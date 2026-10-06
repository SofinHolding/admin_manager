import { useState, type FormEvent } from "react";
import { useAuth } from "../auth";

export default function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Đăng nhập thất bại");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <form onSubmit={submit} className="w-80 rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <h1 className="mb-1 text-lg font-semibold">Local Fallback Tool</h1>
        <p className="mb-4 text-xs text-gray-500">
          Đăng nhập bằng tài khoản admin hoặc dash_admin trên Admin Manager.
        </p>
        <label className="mb-1 block text-xs font-medium text-gray-600">Tên đăng nhập</label>
        <input value={username} onChange={(e) => setUsername(e.target.value)}
               className="mb-3 h-9 w-full rounded-md border border-gray-300 px-3 text-sm outline-none focus:border-blue-500"
               autoFocus />
        <label className="mb-1 block text-xs font-medium text-gray-600">Mật khẩu</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
               className="mb-4 h-9 w-full rounded-md border border-gray-300 px-3 text-sm outline-none focus:border-blue-500" />
        {error && <p className="mb-3 text-xs text-red-600">{error}</p>}
        <button type="submit" disabled={busy || !username || !password}
                className="h-9 w-full rounded-md bg-blue-600 text-sm font-medium text-white disabled:opacity-50">
          {busy ? "Đang đăng nhập…" : "Đăng nhập"}
        </button>
      </form>
    </div>
  );
}
