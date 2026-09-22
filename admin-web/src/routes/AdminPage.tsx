/**
 * Trang quản trị — tạo invite key, quản lý tài khoản.
 *
 * Chỉ admin (role='admin') mới vào được. Viewer bị chặn ở ProtectedRoute hoặc API trả 403.
 * Key tạo ra luôn single-use (max_uses=1) và hết hạn sau 7 ngày — đây là constraint cố định,
 * không cho chọn trên giao diện để tránh tạo key vĩnh viễn.
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Bot, Check, Copy, Key, Pencil, Plus, RefreshCw, ShieldCheck, Trash2, UserX, UserCheck, X,
} from "lucide-react";
import { adminApi, type InviteKey, type Account } from "../api";
import { adminApi as rewardAdminApi } from "../reward/api";
import { Button } from "../components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";

// ── Helpers ─────────────────────────────────────────────────────────────────

function fmtDate(iso: string) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("vi-VN", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function isExpired(expiresAt: string) {
  if (!expiresAt) return false;
  return new Date(expiresAt) < new Date();
}

function keyStatusLabel(k: InviteKey) {
  if (k.status === "revoked") return { text: "Đã thu hồi", cls: "text-red-500" };
  if (isExpired(k.expires_at)) return { text: "Hết hạn", cls: "text-yellow-600" };
  if (k.used_count >= k.max_uses) return { text: "Đã dùng", cls: "text-muted-foreground" };
  return { text: "Hoạt động", cls: "text-green-600" };
}

function userStatusBadge(s: string) {
  switch (s) {
    case "active": return { text: "Hoạt động", cls: "bg-green-500/15 text-green-700" };
    case "suspended": return { text: "Đã khoá", cls: "bg-red-500/15 text-red-600" };
    case "pending": return { text: "Chờ xác thực", cls: "bg-yellow-500/15 text-yellow-700" };
    default: return { text: s, cls: "bg-muted text-muted-foreground" };
  }
}

// ── Component chính ─────────────────────────────────────────────────────────

export default function AdminPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Quản trị hệ thống</h1>
      <Tabs defaultValue="keys">
        <TabsList className="w-full max-w-md">
          <TabsTrigger value="keys"><Key className="mr-1.5 size-4" /> Mã mời</TabsTrigger>
          <TabsTrigger value="users"><ShieldCheck className="mr-1.5 size-4" /> Tài khoản</TabsTrigger>
        </TabsList>
        <TabsContent value="keys"><InviteKeysTab /></TabsContent>
        <TabsContent value="users"><AccountsTab /></TabsContent>
      </Tabs>
    </div>
  );
}

// ── Tab: Mã mời ─────────────────────────────────────────────────────────────

function InviteKeysTab() {
  const [keys, setKeys] = useState<InviteKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [role, setRole] = useState("viewer");
  const [label, setLabel] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { keys: list } = await adminApi.listKeys();
      setKeys(list);
    } catch (e: any) {
      toast.error(e.message || "Không tải được danh sách key");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    setCreating(true);
    try {
      const newKey = await adminApi.createKey(role, label.trim());
      setKeys((prev) => [newKey, ...prev]);
      setLabel("");
      toast.success(`Đã tạo key: ${newKey.key}`);
    } catch (e: any) {
      toast.error(e.message || "Tạo key thất bại");
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (key: string) => {
    try {
      await adminApi.revokeKey(key);
      setKeys((prev) =>
        prev.map((k) => (k.key === key ? { ...k, status: "revoked" } : k)),
      );
      toast.success("Đã thu hồi key");
    } catch (e: any) {
      toast.error(e.message || "Thu hồi thất bại");
    }
  };

  const copyKey = (key: string) => {
    // navigator.clipboard chỉ hoạt động trên HTTPS; fallback execCommand cho HTTP
    if (navigator.clipboard) {
      navigator.clipboard.writeText(key).then(() => toast.success("Đã copy mã mời")).catch(() => copyFallback(key));
    } else {
      copyFallback(key);
    }
  };

  const copyFallback = (text: string) => {
    const el = document.createElement("textarea");
    el.value = text;
    el.style.cssText = "position:fixed;top:-9999px;left:-9999px";
    document.body.appendChild(el);
    el.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(el);
    if (ok) toast.success("Đã copy mã mời"); else toast.error("Không copy được — hãy copy thủ công");
  };

  return (
    <div className="space-y-4">
      {/* Form tạo key */}
      <div className="rounded-xl border border-border bg-elev-1 p-4">
        <h3 className="mb-3 text-sm font-semibold">Tạo mã mời mới</h3>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[140px]">
            <label className="mb-1 block text-xs text-muted-foreground">Vai trò</label>
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger className="h-9 w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="viewer">Viewer</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex-1">
            <label className="mb-1 block text-xs text-muted-foreground">Ghi chú (tuỳ chọn)</label>
            <input
              type="text"
              className="h-9 w-full rounded-md border border-border bg-elev-1 px-3 text-sm outline-none transition-colors focus:border-primary"
              placeholder="VD: Dành cho nhóm marketing"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              maxLength={200}
            />
          </div>
          <Button onClick={handleCreate} disabled={creating} className="h-9 gap-1.5">
            {creating ? <RefreshCw className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Tạo key
          </Button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Key chỉ dùng được <strong>1 lần</strong> và <strong>hết hạn sau 7 ngày</strong>.
        </p>
      </div>

      {/* Danh sách keys */}
      <div className="rounded-xl border border-border bg-elev-1">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h3 className="text-sm font-semibold">Danh sách mã mời ({keys.length})</h3>
          <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>

        {loading && keys.length === 0 ? (
          <div className="p-6 text-center text-sm text-muted-foreground">Đang tải…</div>
        ) : keys.length === 0 ? (
          <div className="p-6 text-center text-sm text-muted-foreground">
            Chưa có mã mời nào. Tạo key đầu tiên ở trên.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="px-4 py-2 font-medium">Mã</th>
                  <th className="px-4 py-2 font-medium">Vai trò</th>
                  <th className="px-4 py-2 font-medium">Ghi chú</th>
                  <th className="px-4 py-2 font-medium">Trạng thái</th>
                  <th className="px-4 py-2 font-medium">Hết hạn</th>
                  <th className="px-4 py-2 font-medium">Tạo lúc</th>
                  <th className="px-4 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => {
                  const st = keyStatusLabel(k);
                  return (
                    <tr key={k.key} className="border-b border-border/50 last:border-0 hover:bg-elev-2/50">
                      <td className="px-4 py-2.5">
                        <button
                          onClick={() => copyKey(k.key)}
                          className="inline-flex items-center gap-1 rounded bg-surface-1 px-2 py-0.5 font-mono text-xs transition-colors hover:bg-primary/10"
                          title="Click để copy"
                        >
                          {k.key}
                          <Copy className="size-3 text-muted-foreground" />
                        </button>
                      </td>
                      <td className="px-4 py-2.5 capitalize">{k.role}</td>
                      <td className="px-4 py-2.5 text-muted-foreground">{k.label || "—"}</td>
                      <td className={`px-4 py-2.5 font-medium ${st.cls}`}>{st.text}</td>
                      <td className="px-4 py-2.5 text-xs text-muted-foreground">{fmtDate(k.expires_at)}</td>
                      <td className="px-4 py-2.5 text-xs text-muted-foreground">{fmtDate(k.created_at)}</td>
                      <td className="px-4 py-2.5">
                        {k.status === "active" && !isExpired(k.expires_at) && k.used_count < k.max_uses && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleRevoke(k.key)}
                            className="text-red-500 hover:text-red-600"
                            title="Thu hồi"
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Tab: Tài khoản ──────────────────────────────────────────────────────────

function AccountsTab() {
  const [users, setUsers] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editData, setEditData] = useState({ username: "", email: "", role: "" });
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { users: list } = await adminApi.listUsers();
      setUsers(list);
    } catch (e: any) {
      toast.error(e.message || "Không tải được danh sách tài khoản");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const startEdit = (u: Account) => {
    setEditingId(u.id);
    setEditData({ username: u.username, email: u.email, role: u.role });
  };

  const cancelEdit = () => {
    setEditingId(null);
  };

  const handleSaveEdit = async (u: Account) => {
    const changes: Record<string, string> = {};
    if (editData.username !== u.username) changes.username = editData.username;
    if (editData.email !== u.email) changes.email = editData.email;
    if (editData.role !== u.role) changes.role = editData.role;

    if (Object.keys(changes).length === 0) {
      setEditingId(null);
      return;
    }

    setSaving(true);
    try {
      await adminApi.updateUser(u.id, changes);
      setUsers((prev) =>
        prev.map((x) => (x.id === u.id ? { ...x, ...changes } : x)),
      );
      setEditingId(null);
      toast.success("Đã cập nhật thông tin tài khoản");
    } catch (e: any) {
      toast.error(e.message || "Cập nhật thất bại");
    } finally {
      setSaving(false);
    }
  };

  const toggleStatus = async (u: Account) => {
    const newStatus = u.status === "suspended" ? "active" : "suspended";
    try {
      await adminApi.setUserStatus(u.id, newStatus);
      setUsers((prev) =>
        prev.map((x) => (x.id === u.id ? { ...x, status: newStatus } : x)),
      );
      toast.success(newStatus === "suspended" ? "Đã khoá tài khoản" : "Đã mở khoá tài khoản");
    } catch (e: any) {
      toast.error(e.message || "Thao tác thất bại");
    }
  };

  const handleDelete = async (u: Account) => {
    if (!confirm(`Xoá tài khoản "${u.username}"? Hành động này không thể hoàn tác.`)) return;
    try {
      await adminApi.deleteUser(u.id);
      setUsers((prev) => prev.filter((x) => x.id !== u.id));
      toast.success("Đã xoá tài khoản");
    } catch (e: any) {
      toast.error(e.message || "Xoá thất bại");
    }
  };

  // Cấp/thu hồi quyền discord — đi qua reward-service (admin-server chỉ nhận viewer|admin nên
  // KHÔNG thể set role='discord' qua API của nó; reward-service UPDATE trực tiếp accounts.role).
  const handleToggleDiscord = async (u: Account) => {
    const granting = u.role !== "discord";
    try {
      if (granting) {
        const r = await rewardAdminApi.grantDiscord(u.id);
        setUsers((prev) => prev.map((x) => (x.id === u.id ? { ...x, role: r.to_role } : x)));
        toast.success(`Đã cấp quyền Discord cho ${u.username}`);
      } else {
        const r = await rewardAdminApi.revokeDiscord(u.id);
        setUsers((prev) => prev.map((x) => (x.id === u.id ? { ...x, role: r.to_role } : x)));
        toast.success(`Đã thu hồi quyền Discord của ${u.username}`);
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Thao tác Discord thất bại");
    }
  };

  const editInput = "h-7 rounded border border-border bg-elev-1 px-2 text-sm outline-none focus:border-primary";

  return (
    <div className="rounded-xl border border-border bg-elev-1">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h3 className="text-sm font-semibold">Danh sách tài khoản ({users.length})</h3>
        <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
        </Button>
      </div>

      {loading && users.length === 0 ? (
        <div className="p-6 text-center text-sm text-muted-foreground">Đang tải…</div>
      ) : users.length === 0 ? (
        <div className="p-6 text-center text-sm text-muted-foreground">
          Chưa có tài khoản nào.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="px-4 py-2 font-medium">Username</th>
                <th className="px-4 py-2 font-medium">Email</th>
                <th className="px-4 py-2 font-medium">Vai trò</th>
                <th className="px-4 py-2 font-medium">Trạng thái</th>
                <th className="px-4 py-2 font-medium">Đăng nhập gần nhất</th>
                <th className="px-4 py-2 font-medium">Tạo lúc</th>
                <th className="px-4 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const badge = userStatusBadge(u.status);
                const isEditing = editingId === u.id;
                return (
                  <tr key={u.id} className="border-b border-border/50 last:border-0 hover:bg-elev-2/50">
                    <td className="px-4 py-2.5 font-medium">
                      {isEditing ? (
                        <input
                          className={editInput + " w-28"}
                          value={editData.username}
                          onChange={(e) => setEditData({ ...editData, username: e.target.value })}
                        />
                      ) : u.username}
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground">
                      {isEditing ? (
                        <input
                          className={editInput + " w-44"}
                          value={editData.email}
                          onChange={(e) => setEditData({ ...editData, email: e.target.value })}
                        />
                      ) : u.email}
                    </td>
                    <td className="px-4 py-2.5 capitalize">
                      {isEditing ? (
                        <select
                          className={editInput + " w-24"}
                          value={editData.role}
                          onChange={(e) => setEditData({ ...editData, role: e.target.value })}
                        >
                          <option value="viewer">Viewer</option>
                          <option value="admin">Admin</option>
                        </select>
                      ) : u.role}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${badge.cls}`}>
                        {badge.text}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground">{fmtDate(u.last_login)}</td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground">{fmtDate(u.created_at)}</td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-1">
                        {isEditing ? (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleSaveEdit(u)}
                              disabled={saving}
                              className="text-green-600"
                              title="Lưu"
                            >
                              {saving ? <RefreshCw className="size-4 animate-spin" /> : <Check className="size-4" />}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={cancelEdit}
                              className="text-muted-foreground"
                              title="Huỷ"
                            >
                              <X className="size-4" />
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => startEdit(u)}
                              className="text-blue-500 hover:text-blue-600"
                              title="Chỉnh sửa"
                            >
                              <Pencil className="size-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleToggleDiscord(u)}
                              title={u.role === "discord" ? "Thu hồi quyền Discord" : "Cấp quyền Discord"}
                              className={u.role === "discord" ? "text-indigo-500 hover:text-indigo-600" : "text-muted-foreground hover:text-indigo-500"}
                            >
                              <Bot className="size-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => toggleStatus(u)}
                              title={u.status === "suspended" ? "Mở khoá" : "Khoá"}
                              className={u.status === "suspended" ? "text-green-600" : "text-yellow-600"}
                            >
                              {u.status === "suspended" ? <UserCheck className="size-4" /> : <UserX className="size-4" />}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleDelete(u)}
                              className="text-red-500 hover:text-red-600"
                              title="Xoá"
                            >
                              <Trash2 className="size-4" />
                            </Button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
