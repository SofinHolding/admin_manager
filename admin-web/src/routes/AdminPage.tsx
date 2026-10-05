/**
 * Trang quản trị — tạo invite key (kèm vai trò Discord), quản lý tài khoản (lọc theo vai trò,
 * cấp/thu quyền Discord), job toàn hệ thống, khoá kênh, nhật ký đổi role.
 *
 * Chỉ admin (role='admin') mới vào được. Viewer bị chặn ở ProtectedRoute hoặc API trả 403.
 * Key tạo ra luôn single-use (max_uses=1) và hết hạn sau 7 ngày — đây là constraint cố định,
 * không cho chọn trên giao diện để tránh tạo key vĩnh viễn.
 *
 * Trang "Reward admin" riêng (/reward/admin) đã gộp hết vào đây — không còn route riêng.
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Bot, Check, Copy, Globe, History, Key, ListChecks, Pencil, Plus, RefreshCw, ShieldCheck,
  Trash2, Unlock, UserX, UserCheck, X,
} from "lucide-react";
import { adminApi, type InviteKey, type Account, type CountryAlias, type CountryCanonical } from "../api";
import {
  adminApi as rewardAdminApi, type JobSummary, type RoleAuditEntry, type RunnerLock,
} from "../reward/api";
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
        <TabsList className="w-full max-w-3xl">
          <TabsTrigger value="keys"><Key className="mr-1.5 size-4" /> Mã mời</TabsTrigger>
          <TabsTrigger value="users"><ShieldCheck className="mr-1.5 size-4" /> Tài khoản</TabsTrigger>
          <TabsTrigger value="jobs"><ListChecks className="mr-1.5 size-4" /> Job toàn hệ thống</TabsTrigger>
          <TabsTrigger value="locks"><Unlock className="mr-1.5 size-4" /> Khoá kênh</TabsTrigger>
          <TabsTrigger value="audit"><History className="mr-1.5 size-4" /> Nhật ký role</TabsTrigger>
          <TabsTrigger value="countries"><Globe className="mr-1.5 size-4" /> Chuẩn hoá quốc gia</TabsTrigger>
        </TabsList>
        <TabsContent value="keys"><InviteKeysTab /></TabsContent>
        <TabsContent value="users"><AccountsTab /></TabsContent>
        <TabsContent value="jobs"><SystemJobsTab /></TabsContent>
        <TabsContent value="locks"><LocksTab /></TabsContent>
        <TabsContent value="audit"><AuditTab /></TabsContent>
        <TabsContent value="countries"><CountryAliasesTab /></TabsContent>
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
                <SelectItem value="dash_admin">Admin Dashboard (ADD)</SelectItem>
                <SelectItem value="discord">Discord</SelectItem>
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
  const [roleFilter, setRoleFilter] = useState("all");

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
  const filteredUsers = roleFilter === "all" ? users : users.filter((u) => u.role === roleFilter);

  return (
    <div className="rounded-xl border border-border bg-elev-1">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <h3 className="text-sm font-semibold">
          Danh sách tài khoản ({filteredUsers.length}{roleFilter !== "all" ? ` / ${users.length}` : ""})
        </h3>
        <div className="flex items-center gap-2">
          <Select value={roleFilter} onValueChange={setRoleFilter}>
            <SelectTrigger className="h-8 w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Tất cả</SelectItem>
              <SelectItem value="viewer">Viewer</SelectItem>
              <SelectItem value="admin">Admin</SelectItem>
              <SelectItem value="dash_admin">Admin Dashboard (ADD)</SelectItem>
              <SelectItem value="discord">Discord</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>

      {loading && users.length === 0 ? (
        <div className="p-6 text-center text-sm text-muted-foreground">Đang tải…</div>
      ) : users.length === 0 ? (
        <div className="p-6 text-center text-sm text-muted-foreground">
          Chưa có tài khoản nào.
        </div>
      ) : filteredUsers.length === 0 ? (
        <div className="p-6 text-center text-sm text-muted-foreground">
          Không có tài khoản nào khớp bộ lọc.
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
              {filteredUsers.map((u) => {
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

// ── Tab: Job toàn hệ thống ───────────────────────────────────────────────────

function SystemJobsTab() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    rewardAdminApi.jobs().then((d) => setJobs(d.jobs)).catch((err) => toast.error((err as Error).message)).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><RefreshCw className="size-4 animate-spin" /> Đang tải…</div>;

  return (
    <div className="overflow-auto rounded-xl border border-border bg-elev-1">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="px-4 py-2 font-medium">Tên</th>
            <th className="px-4 py-2 font-medium">Chủ sở hữu</th>
            <th className="px-4 py-2 font-medium">Trạng thái</th>
            <th className="px-4 py-2 font-medium">Tổng item</th>
            <th className="px-4 py-2 font-medium">Tạo lúc</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id} className="border-b border-border/50 last:border-0 hover:bg-elev-2/50">
              <td className="px-4 py-2.5 font-medium">{j.name}</td>
              <td className="px-4 py-2.5 text-xs">{j.owner_username ?? "—"}</td>
              <td className="px-4 py-2.5 capitalize">{j.status}</td>
              <td className="px-4 py-2.5 text-xs">{j.total_items}</td>
              <td className="px-4 py-2.5 text-xs text-muted-foreground">{new Date(j.created_at).toLocaleString("vi-VN")}</td>
            </tr>
          ))}
          {jobs.length === 0 && <tr><td colSpan={5} className="px-4 py-6 text-center text-muted-foreground">Không có job</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

// ── Tab: Khoá kênh ────────────────────────────────────────────────────────────

function LocksTab() {
  const [locks, setLocks] = useState<RunnerLock[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await rewardAdminApi.locks();
      setLocks(d.locks);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const release = async (jobId: string) => {
    if (!confirm("Giải phóng khoá kênh này? Chỉ làm khi lock đã chết (tuổi > 30s).")) return;
    setBusyId(jobId);
    try {
      await rewardAdminApi.releaseLock(jobId);
      toast.success("Đã giải phóng khoá kênh");
      await load();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  if (loading) return <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><RefreshCw className="size-4 animate-spin" /> Đang tải…</div>;

  return (
    <div className="overflow-auto rounded-xl border border-border bg-elev-1">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="px-4 py-2 font-medium">Job</th>
            <th className="px-4 py-2 font-medium">Channel</th>
            <th className="px-4 py-2 font-medium">PID / Host</th>
            <th className="px-4 py-2 font-medium">Nhịp tim gần nhất</th>
            <th className="px-4 py-2 font-medium">Tuổi (giây)</th>
            <th className="px-4 py-2 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          {locks.map((l) => (
            <tr key={l.job_id} className="border-b border-border/50 last:border-0 hover:bg-elev-2/50">
              <td className="px-4 py-2.5 text-xs">{l.job_id}</td>
              <td className="px-4 py-2.5 text-xs">{l.channel_id}</td>
              <td className="px-4 py-2.5 text-xs">{l.pid} / {l.host}</td>
              <td className="px-4 py-2.5 text-xs">{new Date(l.heartbeat_at).toLocaleString("vi-VN")}</td>
              <td className="px-4 py-2.5 text-xs">{l.age_s}</td>
              <td className="px-4 py-2.5">
                <Button size="sm" variant="outline" disabled={l.age_s <= 30 || busyId === l.job_id} onClick={() => release(l.job_id)}>
                  <Unlock className="size-4" /> Giải phóng
                </Button>
              </td>
            </tr>
          ))}
          {locks.length === 0 && <tr><td colSpan={6} className="px-4 py-6 text-center text-muted-foreground">Không có khoá nào</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

// ── Tab: Nhật ký role ───────────────────────────────────────────────────────

function AuditTab() {
  const [entries, setEntries] = useState<RoleAuditEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    rewardAdminApi.roleAudit({ limit: 200 }).then((d) => setEntries(d.entries)).catch((err) => toast.error((err as Error).message)).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><RefreshCw className="size-4 animate-spin" /> Đang tải…</div>;

  return (
    <div className="flex flex-col divide-y divide-border rounded-xl border border-border bg-elev-1">
      {entries.map((e) => (
        <div key={e.id} className="flex items-center justify-between px-4 py-2.5 text-sm">
          <span>
            <span className="font-medium">{e.username}</span>: {e.from_role} → {e.to_role}
            {e.reason && <span className="text-muted-foreground"> ({e.reason})</span>}
            <span className="text-xs text-muted-foreground"> bởi {e.actor_username}</span>
          </span>
          <span className="text-xs text-muted-foreground">{new Date(e.created_at).toLocaleString("vi-VN")}</span>
        </div>
      ))}
      {entries.length === 0 && <p className="p-4 text-sm text-muted-foreground">Chưa có thay đổi role nào</p>}
    </div>
  );
}

// ── Tab: Chuẩn hoá quốc gia ──────────────────────────────────────────────────
//
// Vấn đề thật: Tool ở nhiều máy khác nhau tự gõ tay tên quốc gia khi cấu hình page/kênh (vd
// "BR"/"Brazil"/"brazil" đều cùng 1 nước) — không validate được từ xa. Bảng này cho admin/dash_admin
// tự gộp các biến thể về đúng 1 tên chuẩn — áp dụng NGAY cho mọi biểu đồ, KHÔNG cần sửa gì bên Tool.

function CountryAliasesTab() {
  const [items, setItems] = useState<CountryAlias[]>([]);
  const [canonicals, setCanonicals] = useState<CountryCanonical[]>([]);
  const [loading, setLoading] = useState(true);
  const [alias, setAlias] = useState("");
  const [canonical, setCanonical] = useState("");
  const [saving, setSaving] = useState(false);

  const [newCanonical, setNewCanonical] = useState("");
  const [savingCanonical, setSavingCanonical] = useState(false);
  const [editingCanonical, setEditingCanonical] = useState<string | null>(null);
  const [editCanonicalValue, setEditCanonicalValue] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([adminApi.listCountryAliases(), adminApi.listCountryCanonicals()])
      .then(([a, c]) => { setItems(a.items); setCanonicals(c.items); })
      .catch((e) => toast.error((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleAdd = async () => {
    if (!alias.trim() || !canonical) return;
    setSaving(true);
    try {
      await adminApi.upsertCountryAlias(alias.trim(), canonical);
      toast.success("Đã lưu — số liệu tính lại ngay (cache đã tự xoá)");
      setAlias(""); setCanonical("");
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (a: string) => {
    try {
      await adminApi.deleteCountryAlias(a);
      toast.success("Đã xoá");
      load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const handleAddCanonical = async () => {
    if (!newCanonical.trim()) return;
    setSavingCanonical(true);
    try {
      await adminApi.createCountryCanonical(newCanonical.trim());
      toast.success("Đã tạo tên chuẩn mới");
      setNewCanonical("");
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSavingCanonical(false);
    }
  };

  const saveEditCanonical = async () => {
    if (!editingCanonical) return;
    const newName = editCanonicalValue.trim();
    if (!newName) return;
    try {
      await adminApi.renameCountryCanonical(editingCanonical, newName);
      toast.success("Đã đổi tên — số liệu tính lại ngay");
      setEditingCanonical(null);
      load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const handleDeleteCanonical = async (name: string) => {
    try {
      await adminApi.deleteCountryCanonical(name);
      toast.success("Đã xoá tên chuẩn");
      load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  // Gộp theo canonical để dễ nhìn — 1 nhóm/1 tên chuẩn, liệt kê hết các biến thể gộp vào đó.
  const groups: Record<string, CountryAlias[]> = {};
  for (const it of items) {
    (groups[it.canonical] ??= []).push(it);
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-elev-1 p-4">
        <h3 className="mb-3 text-sm font-semibold">Tên quốc gia chuẩn</h3>
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-64">
            <label className="mb-1 block text-xs text-muted-foreground">Tên chuẩn mới</label>
            <input
              type="text"
              className="h-9 w-full rounded-md border border-border bg-elev-1 px-3 text-sm outline-none focus:border-primary"
              placeholder="VD: Brazil"
              value={newCanonical}
              onChange={(e) => setNewCanonical(e.target.value)}
              maxLength={100}
              onKeyDown={(e) => e.key === "Enter" && handleAddCanonical()}
            />
          </div>
          <Button onClick={handleAddCanonical} disabled={savingCanonical || !newCanonical.trim()}
                  className="h-9 gap-1.5">
            <Plus className="size-4" /> Tạo tên chuẩn
          </Button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Danh sách tên quốc gia "chính thức" — chọn trực tiếp khi thêm biến thể bên dưới (không gõ
          tay) để không bao giờ gõ sai/lệch hoa-thường tạo nhầm nhóm trùng.
        </p>
        {canonicals.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {canonicals.map((c) => (
              <span key={c.name}
                    className="flex items-center gap-1.5 rounded-full bg-elev-2 px-2.5 py-1 text-xs">
                {editingCanonical === c.name ? (
                  <>
                    <input
                      autoFocus
                      className="h-6 w-32 rounded border border-border bg-elev-1 px-1.5 text-xs outline-none focus:border-primary"
                      value={editCanonicalValue}
                      onChange={(e) => setEditCanonicalValue(e.target.value)}
                      maxLength={100}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveEditCanonical();
                        if (e.key === "Escape") setEditingCanonical(null);
                      }}
                    />
                    <button type="button" onClick={saveEditCanonical}
                            className="text-muted-foreground hover:text-green-500" title="Lưu">
                      <Check className="size-3" />
                    </button>
                    <button type="button" onClick={() => setEditingCanonical(null)}
                            className="text-muted-foreground hover:text-red-500" title="Huỷ">
                      <X className="size-3" />
                    </button>
                  </>
                ) : (
                  <>
                    {c.name}
                    <button type="button"
                            onClick={() => { setEditingCanonical(c.name); setEditCanonicalValue(c.name); }}
                            className="text-muted-foreground hover:text-primary-text" title="Đổi tên">
                      <Pencil className="size-3" />
                    </button>
                    <button type="button" onClick={() => handleDeleteCanonical(c.name)}
                            className="text-muted-foreground hover:text-red-500" title="Xoá tên chuẩn này">
                      <Trash2 className="size-3" />
                    </button>
                  </>
                )}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-border bg-elev-1 p-4">
        <h3 className="mb-3 text-sm font-semibold">Thêm biến thể mới</h3>
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-48">
            <label className="mb-1 block text-xs text-muted-foreground">Biến thể (Tool gõ ra)</label>
            <input
              type="text"
              className="h-9 w-full rounded-md border border-border bg-elev-1 px-3 text-sm outline-none focus:border-primary"
              placeholder="VD: BR, brasil…"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              maxLength={100}
            />
          </div>
          <div className="w-48">
            <label className="mb-1 block text-xs text-muted-foreground">Tên chuẩn muốn gộp về</label>
            <Select value={canonical} onValueChange={setCanonical}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={canonicals.length === 0 ? "Chưa có tên chuẩn" : "Chọn tên chuẩn…"} />
              </SelectTrigger>
              <SelectContent>
                {canonicals.map((c) => (
                  <SelectItem key={c.name} value={c.name}>{c.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button onClick={handleAdd} disabled={saving || !alias.trim() || !canonical} className="h-9 gap-1.5">
            <Plus className="size-4" /> Lưu
          </Button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          {canonicals.length === 0
            ? 'Chưa có tên chuẩn nào — tạo ở mục "Tên quốc gia chuẩn" phía trên trước.'
            : "Tra biến thể không phân biệt hoa/thường, tự bỏ khoảng trắng thừa. Áp dụng ngay cho " +
              "mọi biểu đồ theo quốc gia (cache tự xoá khi lưu) — không cần sửa gì bên Tool."}
        </p>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
          <RefreshCw className="size-4 animate-spin" /> Đang tải…
        </div>
      ) : items.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">Chưa có quy tắc chuẩn hoá nào.</p>
      ) : (
        <div className="space-y-3">
          {Object.entries(groups).sort(([a], [b]) => a.localeCompare(b)).map(([canon, rows]) => (
            <div key={canon} className="rounded-xl border border-border bg-elev-1 p-3">
              <div className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
                <Globe className="size-4 text-primary-text" /> {canon}
              </div>
              <div className="flex flex-wrap gap-2">
                {rows.map((r) => (
                  <span key={r.alias}
                        className="flex items-center gap-1.5 rounded-full bg-elev-2 px-2.5 py-1 text-xs">
                    {r.alias}
                    <button type="button" onClick={() => handleDelete(r.alias)}
                            className="text-muted-foreground hover:text-red-500" title="Xoá biến thể này">
                      <X className="size-3" />
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
