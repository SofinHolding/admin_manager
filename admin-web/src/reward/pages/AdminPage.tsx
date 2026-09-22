/**
 * Trang quản trị reward — cấp/thu quyền Discord, job toàn hệ thống, khoá kênh, nhật ký role.
 * Chỉ role `admin` vào được (chặn ở ProtectedRoute).
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Loader2, ShieldCheck, ShieldOff, Unlock } from "lucide-react";
import {
  adminApi, type AdminAccount, type JobSummary, type RoleAuditEntry, type RunnerLock,
} from "../api";
import { Button } from "../../components/ui/button";
import { Badge } from "../../components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";

export default function AdminPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">Quản trị Reward</h1>
        <p className="text-sm text-muted-foreground">Cấp quyền Discord, giám sát job và khoá kênh toàn hệ thống.</p>
      </div>
      <Tabs defaultValue="accounts">
        <TabsList className="w-fit">
          <TabsTrigger value="accounts">Tài khoản</TabsTrigger>
          <TabsTrigger value="jobs">Job toàn hệ thống</TabsTrigger>
          <TabsTrigger value="locks">Khoá kênh</TabsTrigger>
          <TabsTrigger value="audit">Nhật ký role</TabsTrigger>
        </TabsList>
        <TabsContent value="accounts"><AccountsTab /></TabsContent>
        <TabsContent value="jobs"><SystemJobsTab /></TabsContent>
        <TabsContent value="locks"><LocksTab /></TabsContent>
        <TabsContent value="audit"><AuditTab /></TabsContent>
      </Tabs>
    </div>
  );
}

// ── Tab: Tài khoản ───────────────────────────────────────────────────────

function AccountsTab() {
  const [accounts, setAccounts] = useState<AdminAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await adminApi.listAccounts();
      setAccounts(d.accounts);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const grant = async (a: AdminAccount) => {
    if (!confirm(`Cấp quyền Discord cho "${a.username}"? Vai trò của user này trong admin_manager sẽ hiển thị "discord" — ô chọn role bên đó sẽ trống, đó là bình thường.`)) return;
    setBusyId(a.id);
    try {
      await adminApi.grantDiscord(a.id);
      toast.success(`Đã cấp quyền Discord cho ${a.username}`);
      await load();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const revoke = async (a: AdminAccount) => {
    if (!confirm(`Thu hồi quyền Discord của "${a.username}"? Mọi job đang chạy của user này sẽ bị tạm dừng và cấu hình kết nối Discord sẽ bị thu hồi.`)) return;
    setBusyId(a.id);
    try {
      const r = await adminApi.revokeDiscord(a.id);
      toast.success(`Đã thu hồi — ${r.paused_jobs.length} job bị tạm dừng`);
      await load();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  if (loading) return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Đang tải…</div>;

  return (
    <div className="glass overflow-auto rounded-xl">
      <table className="w-full text-left text-sm">
        <thead className="bg-surface-2 text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Username</th>
            <th className="px-3 py-2">Email</th>
            <th className="px-3 py-2">Role</th>
            <th className="px-3 py-2">Trạng thái</th>
            <th className="px-3 py-2">Discord token?</th>
            <th className="px-3 py-2">Job</th>
            <th className="px-3 py-2">Thao tác</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((a) => (
            <tr key={a.id} className="border-t border-hairline">
              <td className="px-3 py-2 font-medium">{a.username}</td>
              <td className="px-3 py-2 text-xs text-muted-foreground">{a.email}</td>
              <td className="px-3 py-2"><Badge variant={a.role === "discord" ? "default" : a.role === "admin" ? "success" : "secondary"}>{a.role}</Badge></td>
              <td className="px-3 py-2 text-xs">{a.status}</td>
              <td className="px-3 py-2 text-xs">{a.has_credential ? a.credential_status : "—"}</td>
              <td className="px-3 py-2 text-xs">{a.jobs_running}/{a.jobs_total} đang chạy</td>
              <td className="px-3 py-2">
                {a.role === "admin" ? (
                  <span className="text-xs text-muted-foreground">—</span>
                ) : a.role === "discord" ? (
                  <Button size="sm" variant="destructive" disabled={busyId === a.id} onClick={() => revoke(a)}>
                    <ShieldOff className="size-4" /> Thu hồi
                  </Button>
                ) : (
                  <Button size="sm" disabled={busyId === a.id} onClick={() => grant(a)}>
                    <ShieldCheck className="size-4" /> Cấp quyền Discord
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Tab: Job toàn hệ thống ───────────────────────────────────────────────

function SystemJobsTab() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminApi.jobs().then((d) => setJobs(d.jobs)).catch((err) => toast.error((err as Error).message)).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Đang tải…</div>;

  return (
    <div className="glass overflow-auto rounded-xl">
      <table className="w-full text-left text-sm">
        <thead className="bg-surface-2 text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Tên</th>
            <th className="px-3 py-2">Chủ sở hữu</th>
            <th className="px-3 py-2">Trạng thái</th>
            <th className="px-3 py-2">Tổng item</th>
            <th className="px-3 py-2">Tạo lúc</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id} className="border-t border-hairline">
              <td className="px-3 py-2 font-medium">{j.name}</td>
              <td className="px-3 py-2 text-xs">{j.owner_username ?? "—"}</td>
              <td className="px-3 py-2"><Badge>{j.status}</Badge></td>
              <td className="px-3 py-2 text-xs">{j.total_items}</td>
              <td className="px-3 py-2 text-xs text-muted-foreground">{new Date(j.created_at).toLocaleString("vi-VN")}</td>
            </tr>
          ))}
          {jobs.length === 0 && <tr><td colSpan={5} className="px-3 py-6 text-center text-muted-foreground">Không có job</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

// ── Tab: Khoá kênh ────────────────────────────────────────────────────────

function LocksTab() {
  const [locks, setLocks] = useState<RunnerLock[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await adminApi.locks();
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
      await adminApi.releaseLock(jobId);
      toast.success("Đã giải phóng khoá kênh");
      await load();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  if (loading) return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Đang tải…</div>;

  return (
    <div className="glass overflow-auto rounded-xl">
      <table className="w-full text-left text-sm">
        <thead className="bg-surface-2 text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Job</th>
            <th className="px-3 py-2">Channel</th>
            <th className="px-3 py-2">PID / Host</th>
            <th className="px-3 py-2">Nhịp tim gần nhất</th>
            <th className="px-3 py-2">Tuổi (giây)</th>
            <th className="px-3 py-2">Thao tác</th>
          </tr>
        </thead>
        <tbody>
          {locks.map((l) => (
            <tr key={l.job_id} className="border-t border-hairline">
              <td className="px-3 py-2 text-xs">{l.job_id}</td>
              <td className="px-3 py-2 text-xs">{l.channel_id}</td>
              <td className="px-3 py-2 text-xs">{l.pid} / {l.host}</td>
              <td className="px-3 py-2 text-xs">{new Date(l.heartbeat_at).toLocaleString("vi-VN")}</td>
              <td className="px-3 py-2 text-xs">{l.age_s}</td>
              <td className="px-3 py-2">
                <Button size="sm" variant="outline" disabled={l.age_s <= 30 || busyId === l.job_id} onClick={() => release(l.job_id)}>
                  <Unlock className="size-4" /> Giải phóng
                </Button>
              </td>
            </tr>
          ))}
          {locks.length === 0 && <tr><td colSpan={6} className="px-3 py-6 text-center text-muted-foreground">Không có khoá nào</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

// ── Tab: Nhật ký role ─────────────────────────────────────────────────────

function AuditTab() {
  const [entries, setEntries] = useState<RoleAuditEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminApi.roleAudit({ limit: 200 }).then((d) => setEntries(d.entries)).catch((err) => toast.error((err as Error).message)).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Đang tải…</div>;

  return (
    <div className="glass flex flex-col divide-y divide-hairline rounded-xl">
      {entries.map((e) => (
        <div key={e.id} className="flex items-center justify-between px-4 py-2 text-sm">
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
