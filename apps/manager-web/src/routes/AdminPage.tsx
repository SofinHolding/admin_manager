/**
 * Trang quản trị hệ thống (ui-design-v1.md): điều hướng trong trang theo VIỆC —
 *   Mã mời (tạo + danh sách) · Tài khoản (tìm, sửa, quyền, khóa/mở, xóa) · Phân phối điểm (công việc toàn hệ thống +
 *   nhật ký đổi vai trò; cần quyền `reward:admin`) · Khóa kênh (cần `reward:admin`) · Quốc gia (chuẩn hóa tên).
 *
 * Quản trị viên làm mọi việc. Quản lý Dashboard (`dash_admin`) KHÔNG khóa/sửa/xóa tài khoản quản trị viên: giao diện vô hiệu các nút
 * đó kèm lời giải thích; server vẫn là nơi cưỡng chế (403 ADMIN_TARGET_FORBIDDEN, hiển thị bằng lời dễ hiểu).
 * Mã mời: role, ghi chú, số lần dùng tối đa và thời hạn (ngày) do admin chọn khi tạo.
 * Chỉ trình bày: không đổi logic gọi API.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import {
  Ban, Check, CheckCircle2, Clock, Copy, Download, Globe, Lock, Pencil, Plus, Search, ShieldPlus,
  Trash2, Unlock, UserCheck, UserX, X, XCircle, KeyRound, Info,
} from "lucide-react";
import {
  adminApi, integrationApi, type InviteKey, type KeyPreset, type Account, type CountryAlias, type CountryCanonical,
  type IntegrationClient, type IntegrationClientDetail, type PlatformRegistryEntry,
} from "../api";
import {
  adminApi as rewardAdminApi, type JobSummary, type RoleAuditEntry, type RunnerLock,
} from "../reward/api";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Badge } from "../components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../components/ui/dialog";
import { PermissionsDialog } from "../components/PermissionsDialog";
import { EmptyState, Field, Notice, PageHeader, Section } from "../components/kit";
import { useAuth } from "../auth";
import { PERM_REWARD_ADMIN } from "../auth/permissions";
import { roleLabel } from "../nav";
import { SecretOnceDialog, type OneTimeSecret } from "../components/SecretOnceDialog";
import { cn } from "../lib/utils";
import { errorMessage } from "../lib/errors";
import { buildConnectionFile, connectionFileCommand, downloadConnectionFile } from "../lib/connectionFile";
import {
  ADMIN_ONLY_REASON, adminErrorText, CAP_LABEL, SCOPE_LABEL, SHARED_SCOPE_OPTIONS, validateKeyParams,
} from "./adminKeyForm";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";

// ── Helpers ─────────────────────────────────────────────────────────────────

const ROLE_OPTIONS = ["viewer", "dash_admin", "admin", "discord"] as const;
const ASSIGNABLE_ROLES = ["viewer", "dash_admin", "admin"] as const;
const DAY = 86_400_000;

function fmtDate(iso: string | null | undefined, empty = "—") {
  if (!iso) return empty;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return empty;
  return d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function isExpired(expiresAt: string) {
  if (!expiresAt) return false;
  return new Date(expiresAt) < new Date();
}

/** "còn 6 ngày", "còn 3 giờ", "đã hết hạn 2 ngày trước". */
export function relativeExpiry(iso: string, now: number = Date.now()): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = t - now;
  const abs = Math.abs(diff);
  const days = Math.floor(abs / DAY);
  const hours = Math.floor(abs / 3_600_000);
  const span = days >= 1 ? `${days} ngày` : hours >= 1 ? `${hours} giờ` : "dưới 1 giờ";
  if (diff >= 0) return days >= 1 || hours >= 1 ? `còn ${span}` : "còn dưới 1 giờ";
  return days >= 1 || hours >= 1 ? `đã hết hạn ${span} trước` : "vừa hết hạn";
}

type KeyState = "active" | "suspended" | "revoked" | "expired" | "used";

function keyState(k: InviteKey): KeyState {
  if (k.status === "revoked") return "revoked";
  if (k.status === "suspended") return "suspended";
  if (isExpired(k.expires_at)) return "expired";
  if (k.used_count >= k.max_uses) return "used";
  return "active";
}

const KEY_STATE: Record<KeyState, { text: string; variant: "success" | "destructive" | "warning" | "secondary"; icon: typeof Check }> = {
  active: { text: "Dùng được", variant: "success", icon: CheckCircle2 },
  suspended: { text: "Tạm dừng", variant: "warning", icon: Lock },
  revoked: { text: "Đã thu hồi", variant: "destructive", icon: XCircle },
  expired: { text: "Hết hạn", variant: "warning", icon: Clock },
  used: { text: "Đã dùng", variant: "secondary", icon: Ban },
};

const USER_STATUS: Record<string, { text: string; variant: "success" | "destructive" | "warning" | "secondary"; icon: typeof Check }> = {
  active: { text: "Hoạt động", variant: "success", icon: CheckCircle2 },
  suspended: { text: "Đã khóa", variant: "destructive", icon: Lock },
  pending: { text: "Chờ xác thực email", variant: "warning", icon: Clock },
};

const JOB_STATUS: Record<string, string> = {
  draft: "Bản nháp", ready: "Sẵn sàng", queued: "Đang chờ", running: "Đang chạy", paused: "Tạm dừng",
  completed: "Hoàn thành", done: "Hoàn thành", failed: "Có lỗi", cancelled: "Đã hủy", canceled: "Đã hủy",
};

function StatusBadge({ spec }: { spec: { text: string; variant: "success" | "destructive" | "warning" | "secondary"; icon: typeof Check } }) {
  const Icon = spec.icon;
  return <Badge variant={spec.variant}><Icon className="size-3.5" aria-hidden />{spec.text}</Badge>;
}

async function copyText(text: string): Promise<boolean> {
  // navigator.clipboard chỉ chạy trên HTTPS; rơi về execCommand cho HTTP
  try {
    if (navigator.clipboard) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* thử cách dự phòng */ }
  try {
    const el = document.createElement("textarea");
    el.value = text;
    el.style.cssText = "position:fixed;top:-9999px;left:-9999px";
    document.body.appendChild(el);
    el.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(el);
    return ok;
  } catch { return false; }
}

/** Trạng thái "Đã sao chép" tự trả về sau vài giây (không hoạt ảnh). */
function useCopied() {
  const [copied, setCopied] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const copy = async (text: string) => {
    const ok = await copyText(text);
    if (!ok) { toast.error("Không sao chép được. Hãy chọn mã và sao chép thủ công."); return; }
    setCopied(text);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(null), 3000);
  };
  return { copied, copy };
}

function Loading({ children = "Đang tải…" }: { children?: string }) {
  return <p role="status" className="py-6 text-sm text-muted-foreground">{children}</p>;
}

// ── Hộp xác nhận (việc khó hoàn tác) ────────────────────────────────────────

function ConfirmDialog({
  open, title, description, confirmLabel, busy, danger = true, onConfirm, onCancel,
}: {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o && !busy) onCancel(); }}>
      <DialogContent className="max-w-md">
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription className="reading mt-2 text-base">{description}</DialogDescription>
        <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={onCancel} disabled={busy}>Hủy</Button>
          <Button variant={danger ? "destructive" : "default"} onClick={onConfirm} disabled={busy}>
            {busy ? "Đang xử lý…" : confirmLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── Bảng: bảng trên máy tính, danh sách xếp chồng trên điện thoại ───────────

function RTable({ caption, columns, children }: { caption: string; columns: string[]; children: ReactNode }) {
  return (
    <table className="w-full border-collapse text-[0.9375rem]">
      <caption className="sr-only">{caption}</caption>
      <thead className="hidden md:table-header-group">
        <tr className="border-b border-border-strong">
          {columns.map((c, i) => <th key={c + i} scope="col" className="px-3 py-2 text-left text-sm font-semibold text-muted-foreground first:pl-0">{c}</th>)}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
}

function RRow({ children }: { children: ReactNode }) {
  return <tr className="block border-b border-border py-3 last:border-b-0 md:table-row md:py-0">{children}</tr>;
}

function RCell({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <td data-label={label} className={cn(
      "block min-w-0 py-1 md:table-cell md:px-3 md:py-2 md:align-middle md:first:pl-0",
      "before:block before:text-sm before:text-muted-foreground before:content-[attr(data-label)] md:before:hidden",
      className,
    )}>{children}</td>
  );
}

// ── Component chính ─────────────────────────────────────────────────────────

export default function AdminPage() {
  const { user, hasPerm } = useAuth();
  const rewardAdmin = hasPerm(PERM_REWARD_ADMIN);
  const isDashAdmin = user?.role === "dash_admin";
  return (
    <div>
      <PageHeader
        title="Quản trị hệ thống"
        description="Tạo mã mời, quản lý tài khoản và quyền, chuẩn hóa tên quốc gia."
      />
      {isDashAdmin && (
        <Notice tone="info" title="Bạn đang dùng vai trò Quản lý Dashboard" className="mb-6">
          Bạn không khóa, sửa hay xóa được tài khoản quản trị viên. Những tài khoản đó chỉ quản trị viên mới thao tác được.
        </Notice>
      )}
      <Tabs defaultValue="keys">
        <TabsList aria-label="Các việc quản trị" className="w-full">
          <TabsTrigger value="keys">Mã mời</TabsTrigger>
          <TabsTrigger value="users">Tài khoản</TabsTrigger>
          <TabsTrigger value="tools">Công cụ dùng chung</TabsTrigger>
          {rewardAdmin && <TabsTrigger value="rewards">Phân phối điểm</TabsTrigger>}
          {rewardAdmin && <TabsTrigger value="locks">Khóa kênh</TabsTrigger>}
          <TabsTrigger value="countries">Quốc gia</TabsTrigger>
        </TabsList>
        <TabsContent value="keys"><InviteKeysTab /></TabsContent>
        <TabsContent value="users"><AccountsTab isDashAdmin={isDashAdmin} /></TabsContent>
        <TabsContent value="tools"><SharedToolsTab isDashAdmin={isDashAdmin} /></TabsContent>
        {rewardAdmin && <TabsContent value="rewards"><RewardsTab /></TabsContent>}
        {rewardAdmin && <TabsContent value="locks"><LocksTab /></TabsContent>}
        <TabsContent value="countries"><CountryAliasesTab /></TabsContent>
      </Tabs>
    </div>
  );
}

// ── Mã mời ──────────────────────────────────────────────────────────────────

const checkboxCls = "size-4 shrink-0 rounded accent-primary";

function presetLabelFor(k: InviteKey, presets: KeyPreset[]): string {
  if (k.preset) {
    const p = presets.find((x) => x.key === k.preset);
    return p ? p.label : k.preset;
  }
  return `${roleLabel(k.role)} (cũ)`;
}

function InviteKeysTab() {
  const { user: authUser } = useAuth();
  const isDashAdmin = authUser?.role === "dash_admin";
  const [keys, setKeys] = useState<InviteKey[]>([]);
  const [presets, setPresets] = useState<KeyPreset[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [preset, setPreset] = useState("viewer");
  const [label, setLabel] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [created, setCreated] = useState<InviteKey | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<InviteKey | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [showRevoked, setShowRevoked] = useState(false);   // ẩn mặc định: key đã thu hồi không còn dùng được
  const [maxUses, setMaxUses] = useState("1");
  const [expiresDays, setExpiresDays] = useState("7");
  const [suspendTarget, setSuspendTarget] = useState<InviteKey | null>(null);
  const [suspending, setSuspending] = useState(false);
  const { copied, copy } = useCopied();

  // Sửa mã mời (PATCH) — chỉ gửi trường đã đổi.
  const [editTarget, setEditTarget] = useState<InviteKey | null>(null);
  const [editPreset, setEditPreset] = useState("");
  const [editLabel, setEditLabel] = useState("");
  const [editMaxUses, setEditMaxUses] = useState("");
  const [editExpiresDays, setEditExpiresDays] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [editSaving, setEditSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [{ keys: list }, { presets: pr }] = await Promise.all([adminApi.listKeys(), adminApi.listPresets()]);
      setKeys(list);
      setPresets(pr);
      setListError(null);
    } catch (e) {
      setListError(adminErrorText(e, "Không tải được danh sách mã mời. Hãy bấm Tải lại."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const visiblePresets = presets.filter((p) => !(p.admin_only && isDashAdmin));

  const handleCreate = async () => {
    setFormError(null);
    const paramErr = validateKeyParams(maxUses, expiresDays);
    if (paramErr) { setFormError(paramErr); return; }
    if (!preset) { setFormError("Chọn một gói quyền"); return; }
    setCreating(true);
    try {
      const newKey = await adminApi.createKey({ preset, label: label.trim(), max_uses: Number(maxUses), expires_days: Number(expiresDays) });
      setKeys((prev) => [newKey, ...prev]);
      setCreated(newKey);
      setLabel("");
      toast.success("Đã tạo mã mời");
    } catch (e) {
      setFormError(adminErrorText(e, "Không tạo được mã mời. Hãy thử lại."));
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async () => {
    if (!revokeTarget) return;
    const key = revokeTarget.key;
    setRevoking(true);
    try {
      await adminApi.revokeKey(key);
      setKeys((prev) => prev.map((k) => (k.key === key ? { ...k, status: "revoked" } : k)));
      setCreated((c) => (c && c.key === key ? { ...c, status: "revoked" } : c));
      toast.success("Đã thu hồi mã mời");
      setRevokeTarget(null);
    } catch (e) {
      setRevokeTarget(null);
      setListError(adminErrorText(e, "Không thu hồi được mã mời. Hãy thử lại."));
    } finally {
      setRevoking(false);
    }
  };

  const handleSuspend = async () => {
    if (!suspendTarget) return;
    const key = suspendTarget.key;
    const isCurrentlySuspended = suspendTarget.status === "suspended";
    setSuspending(true);
    try {
      if (isCurrentlySuspended) {
        await adminApi.unsuspendKey(key);
        setKeys((prev) => prev.map((k) => (k.key === key ? { ...k, status: "active" } : k)));
        toast.success("Đã khôi phục mã mời");
      } else {
        await adminApi.suspendKey(key);
        setKeys((prev) => prev.map((k) => (k.key === key ? { ...k, status: "suspended" } : k)));
        toast.success("Đã tạm dừng mã mời");
      }
      setSuspendTarget(null);
    } catch (e) {
      setSuspendTarget(null);
      setListError(adminErrorText(e, "Không thực hiện được thao tác. Hãy thử lại."));
    } finally {
      setSuspending(false);
    }
  };

  const startEdit = (k: InviteKey) => {
    setEditTarget(k);
    setEditPreset(k.preset ?? "");
    setEditLabel(k.label);
    setEditMaxUses(String(k.max_uses));
    setEditExpiresDays("");
    setEditError(null);
  };

  const handleSaveEdit = async () => {
    if (!editTarget) return;
    const body: { preset?: string; label?: string; max_uses?: number; expires_days?: number } = {};
    if (editPreset && editPreset !== (editTarget.preset ?? "")) body.preset = editPreset;
    if (editLabel !== editTarget.label) body.label = editLabel;
    if (editMaxUses !== String(editTarget.max_uses)) {
      const n = Number(editMaxUses);
      if (!Number.isInteger(n) || n < 1 || n > 1000) { setEditError("Số lần dùng phải từ 1 đến 1000"); return; }
      body.max_uses = n;
    }
    if (editExpiresDays.trim()) {
      const n = Number(editExpiresDays);
      if (!Number.isInteger(n) || n < 1 || n > 365) { setEditError("Thời hạn phải từ 1 đến 365 ngày"); return; }
      body.expires_days = n;
    }
    if (Object.keys(body).length === 0) { setEditTarget(null); return; }
    setEditSaving(true);
    setEditError(null);
    try {
      const updated = await adminApi.updateKey(editTarget.key, body);
      setKeys((prev) => prev.map((k) => (k.key === updated.key ? updated : k)));
      setEditTarget(null);
      toast.success("Đã sửa mã mời");
    } catch (e) {
      setEditError(adminErrorText(e, "Không sửa được mã mời. Hãy thử lại."));
    } finally {
      setEditSaving(false);
    }
  };
  // Tính trước khi render — tránh IIFE trong JSX vốn làm Vite Fast Refresh mất export default.
  const revokedCount = keys.filter((k) => keyState(k) === "revoked").length;
  const displayed = showRevoked ? keys : keys.filter((k) => keyState(k) !== "revoked");

  return (
    <div>
      <Section title="Tạo mã mời" description="Gửi mã này cho người bạn muốn mời. Họ nhập mã ở trang đăng ký để có tài khoản với gói quyền bạn chọn.">
        <form className="max-w-xl space-y-5" onSubmit={(e) => { e.preventDefault(); void handleCreate(); }}>
          <Field label="Gói quyền" hint="Vai trò và các capability người dùng có sau khi đăng ký bằng mã này.">
            {(p) => (
              <Select value={preset} onValueChange={setPreset}>
                <SelectTrigger {...p} className="md:max-w-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {visiblePresets.map((pr) => <SelectItem key={pr.key} value={pr.key}>{pr.label}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
          </Field>
          {preset && (
            <p className="text-sm text-muted-foreground">
              Quyền: {(presets.find((p) => p.key === preset)?.capabilities ?? []).map((c) => CAP_LABEL[c] ?? c).join(", ") || "—"}
            </p>
          )}
          <Field label="Ghi chú" optional hint="Ghi chú để bạn nhớ mã này dành cho ai. Chỉ quản trị viên thấy.">
            {(p) => <Input {...p} value={label} onChange={(e) => setLabel(e.target.value)} maxLength={200} placeholder="Ví dụ: Dành cho nhóm marketing" />}
          </Field>
          <div className="flex flex-wrap gap-4">
            <Field label="Số lần dùng tối đa" hint="1 đến 1000. Mặc định: 1 lần.">
              {(p) => (
                <Input {...p} type="number" min={1} max={1000} value={maxUses}
                       onChange={(e) => setMaxUses(e.target.value)} className="md:max-w-28" />
              )}
            </Field>
            <Field label="Thời hạn (ngày)" hint="1 đến 365. Mặc định: 7 ngày.">
              {(p) => (
                <Input {...p} type="number" min={1} max={365} value={expiresDays}
                       onChange={(e) => setExpiresDays(e.target.value)} className="md:max-w-28" />
              )}
            </Field>
          </div>

          {formError && <Notice tone="error" title="Chưa tạo được mã mời">{formError}</Notice>}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <Button type="submit" disabled={creating}>
              <Plus aria-hidden /> {creating ? "Đang tạo…" : "Tạo mã mời"}
            </Button>
            <p className="text-sm text-muted-foreground">Mã dùng được {maxUses} lần, hết hạn sau {expiresDays} ngày.</p>
          </div>
        </form>

        {created && (
          <div className="mt-6 max-w-xl border-l-4 border-accent-2 bg-surface-2 px-4 py-4" role="status" aria-label="Mã mời vừa tạo">
            <p className="font-semibold">Đã tạo mã mời. Hãy sao chép và gửi cho người được mời.</p>
            <p className="my-3 break-all font-mono text-2xl font-semibold tracking-wide" data-testid="created-key">{created.key}</p>
            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={() => void copy(created.key)}>
                {copied === created.key ? <Check aria-hidden /> : <Copy aria-hidden />}
                {copied === created.key ? "Đã sao chép" : "Sao chép mã"}
              </Button>
              <span className="text-sm text-muted-foreground">{presetLabelFor(created, presets)}, {relativeExpiry(created.expires_at)}.</span>
            </div>
          </div>
        )}
      </Section>

      <Section
        title={`Danh sách mã mời (${displayed.length})`}
        description={
          <span className="text-sm text-muted-foreground">
            ⚠️ Thu hồi mã mời <strong>không khóa tài khoản đã đăng ký</strong> — chỉ ngăn đăng ký mới bằng mã đó.
          </span>
        }
        actions={
          <div className="flex gap-2">
            {revokedCount > 0 && (
              <Button variant="outline" size="sm" onClick={() => setShowRevoked((v) => !v)}>
                {showRevoked ? `Ẩn đã thu hồi (${revokedCount})` : `Hiện đã thu hồi (${revokedCount})`}
              </Button>
            )}
            <Button variant="outline" onClick={load} disabled={loading}>{loading ? "Đang tải…" : "Tải lại"}</Button>
          </div>
        }
      >
        {listError && <Notice tone="error" className="mb-4">{listError}</Notice>}
        {loading && displayed.length === 0 ? (
          <Loading />
        ) : displayed.length === 0 ? (
          <EmptyState icon={KeyRound} title="Chưa có mã mời nào">Chọn gói quyền ở trên rồi bấm Tạo mã mời để mời người đầu tiên.</EmptyState>
        ) : (
          <RTable caption="Danh sách mã mời" columns={["Mã", "Gói quyền", "Ghi chú", "Trạng thái", "Hạn dùng", "Tạo lúc", ""]}>
            {displayed.map((k) => {
              const st = keyState(k);
              return (
                <RRow key={k.key}>
                  <RCell label="Mã">
                    <button type="button" onClick={() => void copy(k.key)} aria-label={`Sao chép mã ${k.key}`}
                            className="inline-flex min-h-11 items-center gap-2 rounded-md border border-border-strong px-2.5 font-mono text-sm hover:bg-elev-2 md:min-h-10">
                      {k.key}
                      {copied === k.key
                        ? <span className="inline-flex items-center gap-1 font-sans text-success"><Check className="size-4" aria-hidden />Đã sao chép</span>
                        : <Copy className="size-4 text-muted-foreground" aria-hidden />}
                    </button>
                  </RCell>
                  <RCell label="Gói quyền">{presetLabelFor(k, presets)}</RCell>
                  <RCell label="Ghi chú" className="text-muted-foreground">
                    {k.label || "—"}
                    <span className="mt-1 flex flex-wrap gap-1.5 empty:hidden">
                      {k.allowed_apps?.length ? <Badge variant="secondary">Ứng dụng: {k.allowed_apps.join(", ")}</Badge> : null}
                    </span>
                  </RCell>
                  <RCell label="Trạng thái"><StatusBadge spec={KEY_STATE[st]} /></RCell>
                  <RCell label="Hạn dùng">
                    {st === "revoked" ? "—" : <time dateTime={k.expires_at} title={fmtDate(k.expires_at)}>{relativeExpiry(k.expires_at)}</time>}
                  </RCell>
                  <RCell label="Tạo lúc" className="whitespace-nowrap text-muted-foreground">{fmtDate(k.created_at)}</RCell>
                  <RCell label="" className="md:flex md:justify-end md:gap-2">
                    {st !== "revoked" && (
                      <Button variant="outline" size="sm" onClick={() => startEdit(k)} aria-label={`Sửa mã ${k.key}`}>
                        <Pencil aria-hidden /> Sửa
                      </Button>
                    )}
                    {st === "active" && (
                      <Button variant="outline" size="sm" onClick={() => setSuspendTarget(k)} aria-label={`Tạm dừng mã ${k.key}`}>
                        <Lock aria-hidden /> Tạm dừng
                      </Button>
                    )}
                    {st === "suspended" && (
                      <Button variant="outline" size="sm" onClick={() => setSuspendTarget(k)} aria-label={`Khôi phục mã ${k.key}`}>
                        <Unlock aria-hidden /> Khôi phục
                      </Button>
                    )}
                    {st === "active" && (
                      <Button variant="outline" size="sm" onClick={() => setRevokeTarget(k)} aria-label={`Thu hồi mã ${k.key}`}>
                        <Trash2 aria-hidden /> Thu hồi
                      </Button>
                    )}
                  </RCell>
                </RRow>
              );
            })}
          </RTable>
        )}
      </Section>

      <Dialog open={!!editTarget} onOpenChange={(o) => { if (!o && !editSaving) setEditTarget(null); }}>
        <DialogContent className="max-w-lg">
          <DialogTitle>Sửa mã mời {editTarget?.key}</DialogTitle>
          <DialogDescription className="mt-1">Chỉ áp dụng cho lần đăng ký sau, không đổi tài khoản đã tạo.</DialogDescription>
          <div className="mt-4 space-y-4">
            <Field label="Gói quyền" hint="Để trống nếu không đổi.">
              {(p) => (
                <Select value={editPreset || "__keep__"} onValueChange={(v) => setEditPreset(v === "__keep__" ? "" : v)}>
                  <SelectTrigger {...p}><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__keep__">Không đổi</SelectItem>
                    {visiblePresets.map((pr) => <SelectItem key={pr.key} value={pr.key}>{pr.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              )}
            </Field>
            <Field label="Ghi chú">
              {(p) => <Input {...p} value={editLabel} onChange={(e) => setEditLabel(e.target.value)} maxLength={200} />}
            </Field>
            <div className="flex flex-wrap gap-4">
              <Field label="Số lần dùng tối đa">
                {(p) => <Input {...p} type="number" min={1} max={1000} value={editMaxUses} onChange={(e) => setEditMaxUses(e.target.value)} className="md:max-w-28" />}
              </Field>
              <Field label="Thời hạn mới (ngày)" optional hint="Tính từ bây giờ. Để trống nếu không đổi.">
                {(p) => <Input {...p} type="number" min={1} max={365} value={editExpiresDays} onChange={(e) => setEditExpiresDays(e.target.value)} className="md:max-w-28" />}
              </Field>
            </div>
            {editError && <Notice tone="error">{editError}</Notice>}
          </div>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-end">
            <Button variant="outline" onClick={() => setEditTarget(null)} disabled={editSaving}>Hủy</Button>
            <Button onClick={() => void handleSaveEdit()} disabled={editSaving}>{editSaving ? "Đang lưu…" : "Lưu thay đổi"}</Button>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!revokeTarget}
        title="Thu hồi mã mời này?"
        description={<>Mã <span className="font-mono">{revokeTarget?.key}</span> sẽ không dùng để đăng ký được nữa. Người đã đăng ký bằng mã này không bị ảnh hưởng.</>}
        confirmLabel="Thu hồi mã mời"
        busy={revoking}
        onConfirm={handleRevoke}
        onCancel={() => setRevokeTarget(null)}
      />
      <ConfirmDialog
        open={!!suspendTarget}
        title={suspendTarget?.status === "suspended" ? "Khôi phục mã mời này?" : "Tạm dừng mã mời này?"}
        description={suspendTarget?.status === "suspended"
          ? <>Mã <span className="font-mono">{suspendTarget?.key}</span> sẽ dùng được trở lại để đăng ký.</>
          : <>Mã <span className="font-mono">{suspendTarget?.key}</span> sẽ tạm thời không dùng được. Bạn có thể khôi phục lại sau.</>}
        confirmLabel={suspendTarget?.status === "suspended" ? "Khôi phục mã mời" : "Tạm dừng mã mời"}
        busy={suspending}
        danger={suspendTarget?.status !== "suspended"}
        onConfirm={handleSuspend}
        onCancel={() => setSuspendTarget(null)}
      />
    </div>
  );
}

// ── Tài khoản ───────────────────────────────────────────────────────────────

const REVEAL_LABEL: Record<string, string> = {
  pending: "Chờ người dùng nhận",
  revealed: "Đã nhận",
  expired: "Hết hạn nhận",
};

function AccountsTab({ isDashAdmin }: { isDashAdmin: boolean }) {
  const { user: authUser } = useAuth();
  const isAdmin = authUser?.role === "admin";
  const [users, setUsers] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [roleFilter, setRoleFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [permTarget, setPermTarget] = useState<Account | null>(null);
  const [editTarget, setEditTarget] = useState<Account | null>(null);
  const [editData, setEditData] = useState({ username: "", email: "", role: "" });
  const [editError, setEditError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [lockTarget, setLockTarget] = useState<Account | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Account | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Khóa Tool (integration-service, client ownership=personal) — chỉ hiển thị + hành động cho role admin.
  const [clients, setClients] = useState<IntegrationClient[]>([]);
  const [toolBusy, setToolBusy] = useState<string | null>(null);
  const [toolError, setToolError] = useState<string | null>(null);
  const [transferTarget, setTransferTarget] = useState<Account | null>(null);
  const [transferTo, setTransferTo] = useState("");
  const [transferring, setTransferring] = useState(false);
  const [transferError, setTransferError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { users: list } = await adminApi.listUsers();
      setUsers(list);
      setError(null);
    } catch (e) {
      setError(adminErrorText(e, "Không tải được danh sách tài khoản. Hãy bấm Tải lại."));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadClients = useCallback(async () => {
    try {
      const { items } = await integrationApi.listClients();
      setClients(items.filter((c) => c.ownership === "personal"));
      setToolError(null);
    } catch (e) {
      setToolError(errorMessage(e, "Không tải được khóa Tool."));
    }
  }, []);

  useEffect(() => { load(); void loadClients(); }, [load, loadClients]);

  const clientByOwner = useMemo(() => {
    const map: Record<string, IntegrationClient> = {};
    for (const c of clients) map[c.owner_account_id] = c;
    return map;
  }, [clients]);

  const handleReissueTool = async (u: Account) => {
    const c = clientByOwner[u.id];
    if (!c) return;
    setToolBusy(`issue-${u.id}`);
    setToolError(null);
    try {
      const active = c.credentials?.find((k) => k.status === "active");
      if (active) await integrationApi.rotateCredential(c.client_id, active.key_id);
      else await integrationApi.issueCredential(c.client_id);
      toast.success("Đã cấp khóa. Người dùng sẽ nhận khóa trong trang Tích hợp Tool.");
      await loadClients();
    } catch (e) {
      setToolError(errorMessage(e, "Không cấp được khóa."));
    } finally {
      setToolBusy(null);
    }
  };

  const handleRevokeTool = async (u: Account) => {
    const c = clientByOwner[u.id];
    const active = c?.credentials?.find((k) => k.status === "active");
    if (!c || !active) return;
    setToolBusy(`revoke-${u.id}`);
    setToolError(null);
    try {
      await integrationApi.revokeCredential(c.client_id, active.key_id);
      toast.success("Đã thu hồi khóa Tool");
      await loadClients();
    } catch (e) {
      setToolError(errorMessage(e, "Không thu hồi được khóa."));
    } finally {
      setToolBusy(null);
    }
  };

  const handleTransferTool = async () => {
    const c = transferTarget && clientByOwner[transferTarget.id];
    if (!c || !transferTo) return;
    setTransferring(true);
    setTransferError(null);
    try {
      await integrationApi.transferClient(c.client_id, transferTo);
      toast.success("Đã chuyển chủ Tool");
      setTransferTarget(null);
      setTransferTo("");
      await loadClients();
    } catch (e) {
      setTransferError(adminErrorText(e, "Không chuyển được chủ Tool."));
    } finally {
      setTransferring(false);
    }
  };


  const isLocked = (u: Account) => isDashAdmin && u.role === "admin";

  const startEdit = (u: Account) => {
    setEditTarget(u);
    setEditData({ username: u.username, email: u.email, role: u.role });
    setEditError(null);
  };

  const handleSaveEdit = async () => {
    const u = editTarget;
    if (!u) return;
    const changes: Record<string, string> = {};
    if (editData.username !== u.username) changes.username = editData.username;
    if (editData.email !== u.email) changes.email = editData.email;
    if (editData.role !== u.role) changes.role = editData.role;
    if (Object.keys(changes).length === 0) { setEditTarget(null); return; }
    setSaving(true);
    setEditError(null);
    try {
      await adminApi.updateUser(u.id, changes);
      setUsers((prev) => prev.map((x) => (x.id === u.id ? { ...x, ...changes } : x)));
      setEditTarget(null);
      toast.success("Đã cập nhật tài khoản");
    } catch (e) {
      setEditError(adminErrorText(e, "Không cập nhật được tài khoản. Hãy thử lại."));
    } finally {
      setSaving(false);
    }
  };

  const setStatus = async (u: Account, status: "active" | "suspended") => {
    setBusy(true);
    setError(null);
    try {
      await adminApi.setUserStatus(u.id, status);
      setUsers((prev) => prev.map((x) => (x.id === u.id ? { ...x, status } : x)));
      toast.success(status === "suspended" ? "Đã khóa tài khoản" : "Đã mở khóa tài khoản");
    } catch (e) {
      setError(adminErrorText(e, status === "suspended" ? "Không khóa được tài khoản. Hãy thử lại." : "Không mở khóa được tài khoản. Hãy thử lại."));
    } finally {
      setBusy(false);
      setLockTarget(null);
    }
  };

  const handleDelete = async () => {
    const u = deleteTarget;
    if (!u) return;
    setBusy(true);
    setError(null);
    try {
      await adminApi.deleteUser(u.id);
      setUsers((prev) => prev.filter((x) => x.id !== u.id));
      toast.success("Đã xóa tài khoản");
    } catch (e) {
      setError(adminErrorText(e, "Không xóa được tài khoản. Hãy thử lại."));
    } finally {
      setBusy(false);
      setDeleteTarget(null);
    }
  };

  const q = query.trim().toLowerCase();
  const filtered = useMemo(
    () => users.filter((u) => (roleFilter === "all" || u.role === roleFilter)
      && (!q || u.username.toLowerCase().includes(q) || (u.email ?? "").toLowerCase().includes(q))),
    [users, roleFilter, q],
  );

  return (
    <div>
      <Section
        title={`Tài khoản (${filtered.length}${filtered.length !== users.length ? ` / ${users.length}` : ""})`}
        description="Tìm người dùng để sửa thông tin, cấp quyền hoặc khóa tài khoản."
        actions={<Button variant="outline" onClick={load} disabled={loading}>{loading ? "Đang tải…" : "Tải lại"}</Button>}
      >
        <div className="mb-4 flex flex-wrap items-end gap-4">
          <Field label="Tìm tài khoản" className="w-full sm:max-w-xs">
            {(p) => (
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
                <Input {...p} type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Tên đăng nhập hoặc email" className="pl-9" />
              </div>
            )}
          </Field>
          <Field label="Vai trò" className="w-full sm:w-56">
            {(p) => (
              <Select value={roleFilter} onValueChange={setRoleFilter}>
                <SelectTrigger {...p}><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Tất cả vai trò</SelectItem>
                  {ROLE_OPTIONS.map((r) => <SelectItem key={r} value={r}>{roleLabel(r)}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
          </Field>
        </div>

        {error && <Notice tone="error" className="mb-4">{error}</Notice>}
        {toolError && <Notice tone="error" className="mb-4">{toolError}</Notice>}

        {loading && users.length === 0 ? (
          <Loading />
        ) : users.length === 0 ? (
          <EmptyState title="Chưa có tài khoản nào">Tài khoản xuất hiện ở đây sau khi có người đăng ký bằng mã mời.</EmptyState>
        ) : filtered.length === 0 ? (
          <EmptyState title="Không có tài khoản nào khớp"
                      action={<Button variant="outline" onClick={() => { setQuery(""); setRoleFilter("all"); }}>Bỏ lọc</Button>}>
            Thử tên khác hoặc chọn vai trò &quot;Tất cả vai trò&quot;.
          </EmptyState>
        ) : (
          <RTable caption="Danh sách tài khoản" columns={["Tài khoản", "Vai trò", "Trạng thái", "Đăng nhập gần nhất", "Tạo lúc", "Khóa Tool", "Thao tác"]}>
            {filtered.map((u) => {
              const st = USER_STATUS[u.status] ?? { text: u.status, variant: "secondary" as const, icon: Info };
              const locked = isLocked(u);
              const whyId = `why-${u.id}`;
              const suspended = u.status === "suspended";
              return (
                <RRow key={u.id}>
                  <RCell label="Tài khoản">
                    <span className="block font-semibold">{u.username}</span>
                    <span className="block break-all text-sm text-muted-foreground">{u.email}</span>
                  </RCell>
                  <RCell label="Vai trò">
                    <span>{roleLabel(u.role)}</span>
                    {clientByOwner[u.id]?.ownership === "personal" && (
                      <span className="block text-xs text-muted-foreground">+ Tool riêng</span>
                    )}
                  </RCell>
                  <RCell label="Trạng thái"><StatusBadge spec={st} /></RCell>
                  <RCell label="Đăng nhập gần nhất" className="whitespace-nowrap text-muted-foreground">{fmtDate(u.last_login, "Chưa đăng nhập")}</RCell>
                  <RCell label="Tạo lúc" className="whitespace-nowrap text-muted-foreground">{fmtDate(u.created_at)}</RCell>
                  <RCell label="Khóa Tool">
                    {(() => {
                      const c = clientByOwner[u.id];
                      const cred = c?.credentials?.find((k) => k.status === "active");
                      if (!c) {
                        if (u.role === "admin" || u.role === "dash_admin" || u.role === "discord")
                          return <span className="text-xs text-muted-foreground">—</span>;
                        return (
                          <span className="text-xs text-muted-foreground">
                            — <button type="button" className="underline hover:text-foreground"
                                      onClick={() => setPermTarget(u)}>
                                Cấp quyền Tool trong Phân quyền
                              </button>
                          </span>
                        );
                      }
                      return (
                        <div className="space-y-1 text-sm">
                          {!cred ? (
                            <span className="text-muted-foreground">Chưa có khóa</span>
                          ) : (
                            <>
                              <div className="font-mono text-xs">{cred.key_id}</div>
                              <div className="text-muted-foreground">{REVEAL_LABEL[cred.reveal_state ?? "revealed"] ?? "Đã nhận"}</div>
                              <div className="text-muted-foreground">
                                {cred.bound_device_id ? `Thiết bị: ${cred.bound_device_id}` : "Chưa kích hoạt"}
                              </div>
                              <div className="text-muted-foreground">Dùng gần nhất: {fmtDate(cred.last_used_at)}</div>
                            </>
                          )}
                          {isAdmin && (
                            <div className="flex flex-wrap gap-1.5 pt-1">
                              <Button variant="outline" size="sm" disabled={toolBusy === `issue-${u.id}`}
                                      onClick={() => void handleReissueTool(u)}>
                                {toolBusy === `issue-${u.id}` ? "Đang cấp…" : "Cấp lại"}
                              </Button>
                              {cred && (
                                <Button variant="outline" size="sm" className="border-destructive/60 text-destructive"
                                        disabled={toolBusy === `revoke-${u.id}`} onClick={() => void handleRevokeTool(u)}>
                                  {toolBusy === `revoke-${u.id}` ? "Đang thu hồi…" : "Thu hồi"}
                                </Button>
                              )}
                              <Button variant="outline" size="sm" onClick={() => { setTransferTarget(u); setTransferTo(""); setTransferError(null); }}>
                                Chuyển chủ
                              </Button>
                            </div>
                          )}
                        </div>
                      );
                    })()}
                  </RCell>
                  <RCell label="Thao tác">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <div className="contents">
                        <Button variant="outline" size="sm" disabled={locked} aria-describedby={locked ? whyId : undefined}
                                onClick={() => startEdit(u)} aria-label={`Sửa ${u.username}`}>
                          <Pencil aria-hidden /> Sửa
                        </Button>
                        <Button variant="outline" size="sm" disabled={locked} aria-describedby={locked ? whyId : undefined}
                                onClick={() => setPermTarget(u)} aria-label={`Quản lý quyền của ${u.username}`}>
                          <ShieldPlus aria-hidden /> Quyền
                        </Button>
                      </div>
                      <Button variant="outline" size="sm" disabled={locked || busy} aria-describedby={locked ? whyId : undefined}
                              onClick={() => (suspended ? void setStatus(u, "active") : setLockTarget(u))}
                              aria-label={`${suspended ? "Mở khóa" : "Khóa"} ${u.username}`}>
                        {suspended ? <UserCheck aria-hidden /> : <UserX aria-hidden />} {suspended ? "Mở khóa" : "Khóa"}
                      </Button>
                    </div>
                    {locked && (
                      <p id={whyId} className="mt-2 flex items-start gap-1.5 text-sm text-muted-foreground">
                        <Lock className="mt-0.5 size-4 shrink-0" aria-hidden /> {ADMIN_ONLY_REASON}
                      </p>
                    )}
                  </RCell>
                </RRow>
              );
            })}
          </RTable>
        )}
      </Section>

      <PermissionsDialog account={permTarget} onClose={() => setPermTarget(null)} />

      {/* Sửa thông tin; việc xóa nằm riêng ở cuối hộp thoại, tách khỏi nút Lưu */}
      <Dialog open={!!editTarget} onOpenChange={(o) => { if (!o && !saving) setEditTarget(null); }}>
        <DialogContent className="max-w-lg">
          <DialogTitle>Sửa tài khoản {editTarget?.username}</DialogTitle>
          <DialogDescription className="mt-1">Đổi tên đăng nhập, email hoặc vai trò. Đổi vai trò có hiệu lực ở lần đăng nhập kế tiếp.</DialogDescription>
          <div className="mt-4 space-y-4">
            <Field label="Tên đăng nhập">
              {(p) => <Input {...p} value={editData.username} onChange={(e) => setEditData({ ...editData, username: e.target.value })} />}
            </Field>
            <Field label="Email">
              {(p) => <Input {...p} type="email" value={editData.email} onChange={(e) => setEditData({ ...editData, email: e.target.value })} />}
            </Field>
            <Field label="Vai trò">
              {(p) => (
                <Select value={editData.role} onValueChange={(v) => setEditData({ ...editData, role: v })}>
                  <SelectTrigger {...p}><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(ASSIGNABLE_ROLES.includes(editData.role as typeof ASSIGNABLE_ROLES[number]) ? ASSIGNABLE_ROLES : [...ASSIGNABLE_ROLES, editData.role])
                      .map((r) => <SelectItem key={r} value={r}>{roleLabel(r)}</SelectItem>)}
                  </SelectContent>
                </Select>
              )}
            </Field>
            {editError && <Notice tone="error">{editError}</Notice>}
          </div>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-end">
            <Button variant="outline" onClick={() => setEditTarget(null)} disabled={saving}>Hủy</Button>
            <Button onClick={() => void handleSaveEdit()} disabled={saving}>{saving ? "Đang lưu…" : "Lưu thay đổi"}</Button>
          </div>
          <div className="mt-8 border-t border-border pt-5">
            <h2 className="text-base">Xóa tài khoản</h2>
            <p className="mt-1 text-sm text-muted-foreground">Xóa vĩnh viễn tài khoản này. Không khôi phục được.</p>
            <Button variant="outline" className="mt-3 border-destructive/60 text-destructive" disabled={saving}
                    onClick={() => { setDeleteTarget(editTarget); setEditTarget(null); }}>
              <Trash2 aria-hidden /> Xóa tài khoản…
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!lockTarget}
        title={`Khóa tài khoản ${lockTarget?.username ?? ""}?`}
        description="Người này sẽ bị đăng xuất ngay và không đăng nhập lại được cho đến khi bạn mở khóa. Dữ liệu của họ được giữ nguyên."
        confirmLabel="Khóa tài khoản"
        busy={busy}
        onConfirm={() => lockTarget && void setStatus(lockTarget, "suspended")}
        onCancel={() => setLockTarget(null)}
      />
      <ConfirmDialog
        open={!!deleteTarget}
        title={`Xóa tài khoản ${deleteTarget?.username ?? ""}?`}
        description="Người này sẽ bị đăng xuất ngay và tài khoản bị xóa vĩnh viễn. Việc này không hoàn tác được."
        confirmLabel="Xóa tài khoản"
        busy={busy}
        onConfirm={() => void handleDelete()}
        onCancel={() => setDeleteTarget(null)}
      />

      <Dialog open={!!transferTarget} onOpenChange={(o) => { if (!o && !transferring) { setTransferTarget(null); setTransferError(null); } }}>
        <DialogContent className="max-w-lg">
          <DialogTitle>Chuyển chủ Tool của {transferTarget?.username}</DialogTitle>
          <DialogDescription className="mt-1">
            Khóa hiện tại bị thu hồi, chủ mới nhận khóa chờ qua trang Tích hợp Tool. Dữ liệu đã đẩy trước đó vẫn đứng tên chủ cũ.
          </DialogDescription>
          <div className="mt-4 space-y-4">
            <Field label="Chuyển cho tài khoản">
              {(p) => (
                <Select value={transferTo} onValueChange={setTransferTo}>
                  <SelectTrigger {...p}><SelectValue placeholder="Chọn tài khoản đích" /></SelectTrigger>
                  <SelectContent>
                    {users.filter((x) => x.id !== transferTarget?.id && x.status === "active" && !clientByOwner[x.id])
                      .map((x) => <SelectItem key={x.id} value={x.id}>{x.username}</SelectItem>)}
                  </SelectContent>
                </Select>
              )}
            </Field>
            {transferError && <Notice tone="error">{transferError}</Notice>}
          </div>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-end">
            <Button variant="outline" onClick={() => setTransferTarget(null)} disabled={transferring}>Hủy</Button>
            <Button onClick={() => void handleTransferTool()} disabled={transferring || !transferTo}>
              {transferring ? "Đang chuyển…" : "Chuyển chủ"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Phân phối điểm: công việc toàn hệ thống + nhật ký đổi vai trò ───────────

function RewardsTab() {
  return (
    <div>
      <SystemJobsSection />
      <AuditSection />
    </div>
  );
}

function SystemJobsSection() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    rewardAdminApi.jobs().then((d) => setJobs(d.jobs)).catch((e) => setError(errorMessage(e, "Không tải được công việc phân phối điểm."))).finally(() => setLoading(false));
  }, []);

  return (
    <Section title="Công việc phân phối điểm toàn hệ thống" description="Mọi công việc của mọi người dùng, để theo dõi và hỗ trợ.">
      {error && <Notice tone="error" className="mb-4">{error}</Notice>}
      {loading ? <Loading /> : jobs.length === 0 ? (
        <EmptyState title="Chưa có công việc nào">Công việc xuất hiện ở đây khi có người tạo ở mục Công việc phân phối.</EmptyState>
      ) : (
        <RTable caption="Công việc phân phối điểm toàn hệ thống" columns={["Tên", "Chủ sở hữu", "Trạng thái", "Số mục", "Tạo lúc"]}>
          {jobs.map((j) => (
            <RRow key={j.id}>
              <RCell label="Tên" className="font-semibold">{j.name}</RCell>
              <RCell label="Chủ sở hữu">{j.owner_username ?? "—"}</RCell>
              <RCell label="Trạng thái">{JOB_STATUS[j.status] ?? j.status}</RCell>
              <RCell label="Số mục" className="tabular-nums">{j.total_items.toLocaleString("vi-VN")}</RCell>
              <RCell label="Tạo lúc" className="whitespace-nowrap text-muted-foreground">{fmtDate(j.created_at)}</RCell>
            </RRow>
          ))}
        </RTable>
      )}
    </Section>
  );
}

function AuditSection() {
  const [entries, setEntries] = useState<RoleAuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    rewardAdminApi.roleAudit({ limit: 200 }).then((d) => setEntries(d.entries)).catch((e) => setError(errorMessage(e, "Không tải được nhật ký đổi vai trò."))).finally(() => setLoading(false));
  }, []);

  return (
    <Section title="Nhật ký đổi vai trò" description="Ai đã đổi vai trò của ai, lúc nào (tối đa 200 lần gần nhất).">
      {error && <Notice tone="error" className="mb-4">{error}</Notice>}
      {loading ? <Loading /> : entries.length === 0 ? (
        <EmptyState title="Chưa có lần đổi vai trò nào" />
      ) : (
        <ul className="divide-y divide-border border-y border-border">
          {entries.map((e) => (
            <li key={e.id} className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 py-3">
              <span>
                <span className="font-semibold">{e.username}</span>: từ {roleLabel(e.from_role)} sang {roleLabel(e.to_role)}
                {e.reason && <span className="text-muted-foreground"> ({e.reason})</span>}
                <span className="text-muted-foreground"> — bởi {e.actor_username}</span>
              </span>
              <span className="text-sm text-muted-foreground">{fmtDate(e.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// ── Khóa kênh ───────────────────────────────────────────────────────────────

function LocksTab() {
  const [locks, setLocks] = useState<RunnerLock[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState<RunnerLock | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await rewardAdminApi.locks();
      setLocks(d.locks);
      setError(null);
    } catch (e) {
      setError(errorMessage(e, "Không tải được danh sách khóa kênh."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const release = async () => {
    if (!target) return;
    setBusy(true);
    try {
      await rewardAdminApi.releaseLock(target.job_id);
      toast.success("Đã giải phóng khóa kênh");
      setTarget(null);
      await load();
    } catch (e) {
      setTarget(null);
      setError(errorMessage(e, "Không giải phóng được khóa kênh. Hãy thử lại."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section
      title="Khóa kênh"
      description="Khi một công việc đang chạy trên kênh nào, kênh đó bị khóa để không chạy trùng. Nếu tiến trình chết, khóa có thể bị kẹt và cần giải phóng tay."
      actions={<Button variant="outline" onClick={load} disabled={loading}>{loading ? "Đang tải…" : "Tải lại"}</Button>}
    >
      {error && <Notice tone="error" className="mb-4">{error}</Notice>}
      {loading && locks.length === 0 ? <Loading /> : locks.length === 0 ? (
        <EmptyState icon={Unlock} title="Không có khóa nào đang giữ">Khi có công việc đang chạy, khóa của nó hiện ở đây.</EmptyState>
      ) : (
        <RTable caption="Khóa kênh đang giữ" columns={["Công việc", "Kênh", "Tiến trình / máy", "Nhịp tim gần nhất", "Tuổi khóa", ""]}>
          {locks.map((l) => {
            const young = l.age_s <= 30;
            const hintId = `lock-${l.job_id}`;
            return (
              <RRow key={l.job_id}>
                <RCell label="Công việc" className="break-all font-mono text-sm">{l.job_id}</RCell>
                <RCell label="Kênh" className="break-all">{l.channel_id}</RCell>
                <RCell label="Tiến trình / máy">{l.pid} / {l.host}</RCell>
                <RCell label="Nhịp tim gần nhất" className="text-muted-foreground">{fmtDate(l.heartbeat_at)}</RCell>
                <RCell label="Tuổi khóa" className="tabular-nums">{l.age_s} giây</RCell>
                <RCell label="">
                  <Button variant="outline" disabled={young || busy} aria-describedby={young ? hintId : undefined} onClick={() => setTarget(l)}
                          aria-label={`Giải phóng khóa của công việc ${l.job_id}`}>
                    <Unlock aria-hidden /> Giải phóng
                  </Button>
                  {young && <p id={hintId} className="mt-1 text-sm text-muted-foreground">Khóa còn mới, chỉ giải phóng được sau 30 giây.</p>}
                </RCell>
              </RRow>
            );
          })}
        </RTable>
      )}
      <ConfirmDialog
        open={!!target}
        title="Giải phóng khóa kênh này?"
        description="Chỉ làm khi tiến trình giữ khóa đã chết (khóa quá 30 giây không có nhịp tim). Nếu nó vẫn đang chạy, hai tiến trình có thể cùng xử lý một kênh."
        confirmLabel="Giải phóng khóa"
        busy={busy}
        onConfirm={() => void release()}
        onCancel={() => setTarget(null)}
      />
    </Section>
  );
}

// ── Quốc gia: chuẩn hóa tên ─────────────────────────────────────────────────
//
// Vấn đề thật: Tool ở nhiều máy khác nhau tự gõ tay tên quốc gia khi cấu hình page/kênh (vd
// "BR"/"Brazil"/"brazil" đều cùng 1 nước) — không validate được từ xa. Mục này cho quản trị tự gộp các cách viết
// về đúng 1 tên chuẩn — áp dụng NGAY cho mọi biểu đồ, KHÔNG cần sửa gì bên Tool.

function CountryAliasesTab() {
  const [items, setItems] = useState<CountryAlias[]>([]);
  const [canonicals, setCanonicals] = useState<CountryCanonical[]>([]);
  const [loading, setLoading] = useState(true);
  const [alias, setAlias] = useState("");
  const [canonical, setCanonical] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [newCanonical, setNewCanonical] = useState("");
  const [savingCanonical, setSavingCanonical] = useState(false);
  const [editingCanonical, setEditingCanonical] = useState<string | null>(null);
  const [editCanonicalValue, setEditCanonicalValue] = useState("");
  const [deleteCanonical, setDeleteCanonical] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([adminApi.listCountryAliases(), adminApi.listCountryCanonicals()])
      .then(([a, c]) => { setItems(a.items); setCanonicals(c.items); setError(null); })
      .catch((e) => setError(errorMessage(e, "Không tải được danh sách quốc gia.")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleAdd = async () => {
    if (!alias.trim() || !canonical) return;
    setSaving(true);
    try {
      await adminApi.upsertCountryAlias(alias.trim(), canonical);
      toast.success("Đã lưu cách viết. Số liệu được tính lại ngay.");
      setAlias(""); setCanonical("");
      load();
    } catch (e) {
      setError(errorMessage(e, "Không lưu được cách viết này. Hãy thử lại."));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (a: string) => {
    try {
      await adminApi.deleteCountryAlias(a);
      toast.success("Đã bỏ cách viết");
      load();
    } catch (e) {
      setError(errorMessage(e, "Không bỏ được cách viết này. Hãy thử lại."));
    }
  };

  const handleAddCanonical = async () => {
    if (!newCanonical.trim()) return;
    setSavingCanonical(true);
    try {
      await adminApi.createCountryCanonical(newCanonical.trim());
      toast.success("Đã tạo tên chuẩn");
      setNewCanonical("");
      load();
    } catch (e) {
      setError(errorMessage(e, "Không tạo được tên chuẩn. Hãy thử lại."));
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
      toast.success("Đã đổi tên chuẩn. Số liệu được tính lại ngay.");
      setEditingCanonical(null);
      load();
    } catch (e) {
      setError(errorMessage(e, "Không đổi được tên chuẩn. Hãy thử lại."));
    }
  };

  const confirmDeleteCanonical = async () => {
    if (!deleteCanonical) return;
    setDeleting(true);
    try {
      await adminApi.deleteCountryCanonical(deleteCanonical);
      toast.success("Đã xóa tên chuẩn");
      load();
    } catch (e) {
      setError(errorMessage(e, "Không xóa được tên chuẩn. Hãy thử lại."));
    } finally {
      setDeleting(false);
      setDeleteCanonical(null);
    }
  };

  // Gộp theo tên chuẩn để dễ nhìn: mỗi tên chuẩn một khối, liệt kê các cách viết đã gộp vào đó.
  const groups: Record<string, CountryAlias[]> = {};
  for (const it of items) (groups[it.canonical] ??= []).push(it);
  const names = Array.from(new Set([...canonicals.map((c) => c.name), ...Object.keys(groups)])).sort((a, b) => a.localeCompare(b));

  return (
    <div>
      {error && <Notice tone="error" className="mb-6">{error}</Notice>}

      <Section title="Tên quốc gia chuẩn" description="Danh sách tên chính thức. Khi gộp cách viết khác, bạn chọn từ danh sách này nên không gõ sai hay lệch hoa thường.">
        <form className="flex max-w-xl flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); void handleAddCanonical(); }}>
          <Field label="Tên chuẩn mới" className="min-w-0 flex-1 basis-56">
            {(p) => <Input {...p} value={newCanonical} onChange={(e) => setNewCanonical(e.target.value)} maxLength={100} placeholder="Ví dụ: Brazil" />}
          </Field>
          <Button type="submit" disabled={savingCanonical || !newCanonical.trim()}><Plus aria-hidden /> Tạo tên chuẩn</Button>
        </form>
      </Section>

      <Section title="Gộp cách viết khác về tên chuẩn" description="Công cụ ở các máy khác nhau có thể gõ cùng một nước theo nhiều cách (BR, brasil...). Gộp về một tên để biểu đồ không bị tách đôi.">
        <form className="flex max-w-2xl flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); void handleAdd(); }}>
          <Field label="Cách viết khác" className="min-w-0 flex-1 basis-48">
            {(p) => <Input {...p} value={alias} onChange={(e) => setAlias(e.target.value)} maxLength={100} placeholder="Ví dụ: BR, brasil" />}
          </Field>
          <Field label="Gộp về tên chuẩn" className="min-w-0 flex-1 basis-48">
            {(p) => (
              <Select value={canonical} onValueChange={setCanonical}>
                <SelectTrigger {...p}><SelectValue placeholder={canonicals.length === 0 ? "Chưa có tên chuẩn" : "Chọn tên chuẩn"} /></SelectTrigger>
                <SelectContent>
                  {canonicals.map((c) => <SelectItem key={c.name} value={c.name}>{c.name}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
          </Field>
          <Button type="submit" disabled={saving || !alias.trim() || !canonical}><Plus aria-hidden /> Lưu cách viết</Button>
        </form>
        <p className="mt-3 text-sm text-muted-foreground">
          {canonicals.length === 0
            ? "Chưa có tên chuẩn nào. Hãy tạo tên chuẩn ở phần trên trước."
            : "Ô đầu là cách viết do công cụ gõ ra (không phân biệt hoa thường). Áp dụng ngay cho mọi biểu đồ theo quốc gia, không cần sửa gì bên công cụ."}
        </p>
      </Section>

      <Section title="Quy tắc đang áp dụng">
        {loading ? <Loading /> : names.length === 0 ? (
          <EmptyState icon={Globe} title="Chưa có quy tắc nào">Tạo tên chuẩn đầu tiên ở trên, rồi gộp các cách viết khác về tên đó.</EmptyState>
        ) : (
          <ul className="divide-y divide-border border-y border-border">
            {names.map((name) => {
              const rows = groups[name] ?? [];
              const editing = editingCanonical === name;
              return (
                <li key={name} className="py-4">
                  {editing ? (
                    <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); void saveEditCanonical(); }}>
                      <Field label={`Tên mới thay cho ${name}`} className="min-w-0 flex-1 basis-56">
                        {(p) => <Input {...p} autoFocus value={editCanonicalValue} maxLength={100}
                                       onChange={(e) => setEditCanonicalValue(e.target.value)}
                                       onKeyDown={(e) => { if (e.key === "Escape") setEditingCanonical(null); }} />}
                      </Field>
                      <Button type="submit" disabled={!editCanonicalValue.trim()}><Check aria-hidden /> Lưu tên</Button>
                      <Button type="button" variant="outline" onClick={() => setEditingCanonical(null)}>Hủy</Button>
                    </form>
                  ) : (
                    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
                      <h3 className="flex items-center gap-2 text-base font-semibold"><Globe className="size-4 text-primary-text" aria-hidden /> {name}</h3>
                      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
                        <Button variant="outline" aria-label={`Đổi tên ${name}`} onClick={() => { setEditingCanonical(name); setEditCanonicalValue(name); }}>
                          <Pencil aria-hidden /> Đổi tên
                        </Button>
                        <Button variant="outline" className="border-destructive/60 text-destructive" aria-label={`Xóa tên chuẩn ${name}`}
                                onClick={() => setDeleteCanonical(name)}>
                          <Trash2 aria-hidden /> Xóa
                        </Button>
                      </div>
                    </div>
                  )}
                  <div className="mt-3">
                    {rows.length === 0 ? (
                      <p className="text-sm text-muted-foreground">Chưa gộp cách viết khác nào về tên này.</p>
                    ) : (
                      <ul className="flex flex-wrap gap-2" aria-label={`Cách viết đã gộp về ${name}`}>
                        {rows.map((r) => (
                          <li key={r.alias} className="inline-flex items-center rounded-md border border-border-strong bg-surface-2 pl-3">
                            {r.alias}
                            <button type="button" onClick={() => void handleDelete(r.alias)} aria-label={`Bỏ cách viết ${r.alias}`}
                                    className="grid size-11 place-items-center text-muted-foreground hover:text-destructive md:size-10">
                              <X className="size-4" aria-hidden />
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      <ConfirmDialog
        open={!!deleteCanonical}
        title={`Xóa tên chuẩn ${deleteCanonical ?? ""}?`}
        description="Tên này bị bỏ khỏi danh sách và áp dụng ngay cho mọi biểu đồ theo quốc gia. Việc này không hoàn tác được; muốn dùng lại bạn phải tạo lại."
        confirmLabel="Xóa tên chuẩn"
        busy={deleting}
        onConfirm={() => void confirmDeleteCanonical()}
        onCancel={() => setDeleteCanonical(null)}
      />
    </div>
  );
}

// ── Công cụ dùng chung (shared integration clients) ─────────────────────────
// Wizard 4 bước bắt buộc do SERVER giữ trạng thái (draft → configured → active → cấp khóa).
// dash_admin chỉ xem, không được tạo/cấu hình/kích hoạt/cấp khóa.

const CLIENT_KIND_LABEL: Record<string, string> = { fallback_tool: "Công cụ dự phòng", service: "Dịch vụ tự động" };

function wizardStep(c: IntegrationClient): 1 | 2 | 3 | 4 {
  if (c.status === "active") return 4;
  if (c.configured_at) return 3;
  return 2;
}

function SharedToolsTab({ isDashAdmin }: { isDashAdmin: boolean }) {
  const [clients, setClients] = useState<IntegrationClient[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState("");
  const [platforms, setPlatforms] = useState<PlatformRegistryEntry[]>([]);

  // Bước 1 — tạo nháp
  const [toolName, setToolName] = useState("");
  const [kind, setKind] = useState<"fallback_tool" | "service">("fallback_tool");
  const [purpose, setPurpose] = useState("");
  const [creating, setCreating] = useState(false);

  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, IntegrationClientDetail>>({});

  // Bước 2 — cấu hình
  const [cfgPlatforms, setCfgPlatforms] = useState<string[]>([]);
  const [cfgScopes, setCfgScopes] = useState<string[]>([]);
  const [cfgSaving, setCfgSaving] = useState(false);

  // Bước 3 — kích hoạt
  const [confirmName, setConfirmName] = useState("");
  const [activating, setActivating] = useState(false);

  // Bước 4 — cấp khóa
  const [issuing, setIssuing] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<{ clientId: string; keyId: string } | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [secret, setSecret] = useState<OneTimeSecret | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setLoadErr("");
    try {
      const [{ items }, platformsRes] = await Promise.all([
        integrationApi.listClients(),
        integrationApi.listPlatformRegistry().catch(() => ({ items: [] as PlatformRegistryEntry[] })),
      ]);
      setClients(items.filter((c) => c.ownership === "shared"));
      setPlatforms(platformsRes.items);
    } catch (e) {
      setLoadErr(errorMessage(e, "Không tải được danh sách công cụ dùng chung."));
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const loadDetail = useCallback(async (clientId: string) => {
    try {
      const d = await integrationApi.getClient(clientId);
      setDetails((prev) => ({ ...prev, [clientId]: d }));
      setCfgPlatforms(d.allowed_platforms);
      setCfgScopes(d.allowed_scopes);
    } catch { /* ignore */ }
  }, []);

  const toggleExpand = (c: IntegrationClient) => {
    if (expanded === c.client_id) { setExpanded(null); return; }
    setExpanded(c.client_id);
    setConfirmName("");
    setCfgPlatforms(c.allowed_platforms);
    setCfgScopes(c.allowed_scopes);
    void loadDetail(c.client_id);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!toolName.trim() || !purpose.trim()) return;
    setCreating(true);
    try {
      const c = await integrationApi.createSharedClient({ tool_name: toolName.trim(), client_kind: kind, purpose: purpose.trim() });
      toast.success(`Đã tạo nháp "${c.tool_name}". Tiếp tục ở bước Cấu hình.`);
      setToolName(""); setPurpose("");
      await load();
      setExpanded(c.client_id);
      void loadDetail(c.client_id);
    } catch (e2) {
      toast.error(errorMessage(e2, "Không tạo được công cụ. Kiểm tra tên và mục đích."));
    } finally { setCreating(false); }
  };

  const handleConfigure = async (c: IntegrationClient) => {
    if (!cfgPlatforms.length || !cfgScopes.length) { toast.error("Chọn ít nhất một nền tảng và một quyền."); return; }
    setCfgSaving(true);
    try {
      await integrationApi.configureClient(c.client_id, { allowed_platforms: cfgPlatforms, allowed_scopes: cfgScopes });
      toast.success("Đã lưu cấu hình. Tiếp tục ở bước Xem lại + kích hoạt.");
      await load();
      void loadDetail(c.client_id);
    } catch (e) {
      toast.error(errorMessage(e, "Không lưu được cấu hình."));
    } finally { setCfgSaving(false); }
  };

  const handleActivate = async (c: IntegrationClient) => {
    setActivating(true);
    try {
      await integrationApi.activateClient(c.client_id, confirmName.trim());
      toast.success(`Đã kích hoạt "${c.tool_name}". Tiếp tục ở bước Cấp khóa.`);
      await load();
      void loadDetail(c.client_id);
    } catch (e) {
      toast.error(errorMessage(e, "Không kích hoạt được. Kiểm tra lại tên gõ xác nhận."));
    } finally { setActivating(false); }
  };

  const handleIssue = async (c: IntegrationClient) => {
    setIssuing(true);
    try {
      const issued = await integrationApi.issueCredential(c.client_id);
      if (issued.api_key) {
        const file = buildConnectionFile({
          gateway: window.location.origin,
          toolName: c.tool_name,
          clientId: issued.client_id,
          keyId: issued.key_id,
          apiKey: issued.api_key,
          allowedPlatforms: issued.allowed_platforms,
          contractVersion: issued.contract_version,
        });
        setSecret({
          title: "Khóa kết nối mới — sao chép ngay", label: "Khóa kết nối", value: issued.api_key,
          details: [{ label: "key_id", value: issued.key_id }, { label: "quyền", value: issued.scopes.join(", ") }],
          extra: (
            <div className="mt-4 border-t border-border pt-3">
              <Button variant="outline" onClick={() => downloadConnectionFile(file)}>
                <Download aria-hidden /> Tải file kết nối (.json)
              </Button>
              <p className="mt-2 text-sm text-muted-foreground">
                File chứa khóa bí mật — giữ riêng tư, chỉ hiện một lần. Chạy Tool với:
              </p>
              <code className="mt-1 block break-all rounded-md border border-border-strong bg-surface-2 px-3 py-2 font-mono text-xs">
                {connectionFileCommand(file.client_id)}
              </code>
            </div>
          ),
        });
      }
      void loadDetail(c.client_id);
    } catch (e) {
      toast.error(errorMessage(e, "Không tạo được khóa kết nối."));
    } finally { setIssuing(false); }
  };

  const handleRevoke = async () => {
    if (!revokeTarget) return;
    const { clientId, keyId } = revokeTarget;
    setRevoking(true);
    try {
      await integrationApi.revokeCredential(clientId, keyId);
      toast.success("Đã thu hồi khóa.");
      setRevokeTarget(null);
      void loadDetail(clientId);
    } catch (e) {
      toast.error(errorMessage(e, "Không thu hồi được."));
    } finally { setRevoking(false); }
  };

  const stepBadge = (n: 1 | 2 | 3 | 4, label: string, step: number) => (
    <span className={cn("inline-flex items-center gap-1.5 text-sm", n < step ? "text-success" : n === step ? "font-semibold" : "text-muted-foreground")}>
      {n < step ? <CheckCircle2 className="size-4" aria-hidden /> : <span className="inline-grid size-4 place-items-center rounded-full border border-current text-xs">{n}</span>}
      {label}
    </span>
  );

  return (
    <div>
      {isDashAdmin && (
        <Notice tone="info" className="mb-6">Bạn đang xem ở chế độ chỉ đọc. Chỉ quản trị viên (admin) tạo, cấu hình, kích hoạt hoặc cấp khóa được công cụ dùng chung.</Notice>
      )}

      {!isDashAdmin && (
        <Section title="Bước 1 — Tạo công cụ dùng chung" description="Công cụ dùng chung (fallback_tool/service) do admin quản lý, dữ liệu ghi qua X-Delegated-Job — không đứng tên admin.">
          <form onSubmit={(e) => { void handleCreate(e); }} className="flex flex-wrap items-end gap-3">
            <Field label="Tên công cụ">
              {(p) => <Input {...p} value={toolName} onChange={(e) => setToolName(e.target.value)} placeholder="Fallback Collector" required className="w-56" />}
            </Field>
            <Field label="Loại">
              {(p) => (
                <Select value={kind} onValueChange={(v) => setKind(v as typeof kind)}>
                  <SelectTrigger {...p} className="w-48"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="fallback_tool">
                      <span className="font-medium">Công cụ dự phòng</span>
                      <span className="ml-2 text-xs text-muted-foreground">— nhận job từ hàng đợi, thu thập hộ user</span>
                    </SelectItem>
                    <SelectItem value="service">
                      <span className="font-medium">Dịch vụ tự động</span>
                      <span className="ml-2 text-xs text-muted-foreground">— tích hợp API theo lịch/sự kiện</span>
                    </SelectItem>
                  </SelectContent>
                </Select>
              )}
            </Field>
            <Field label="Mục đích" className="min-w-64 flex-1">
              {(p) => <Input {...p} value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="Thu thập dữ liệu hộ user khi primary tool lỗi" required />}
            </Field>
            <Button type="submit" disabled={creating || !toolName.trim() || !purpose.trim()}>
              <Plus aria-hidden /> {creating ? "Đang tạo…" : "Tạo nháp"}
            </Button>
          </form>
        </Section>
      )}

      <Section
        title="Danh sách công cụ dùng chung"
        description="Bấm vào tên công cụ (▼) để xem và tiếp tục các bước 2–4. Server giữ trạng thái — không cho nhảy bước."
        actions={<Button variant="outline" onClick={() => void load()} disabled={loading}>{loading ? "Đang tải…" : "Tải lại"}</Button>}
      >
        {loadErr && <Notice tone="error" className="mb-4">{loadErr}</Notice>}
        {loading && clients.length === 0 ? (
          <Loading />
        ) : clients.length === 0 ? (
          <EmptyState icon={KeyRound} title="Chưa có công cụ dùng chung nào">Tạo một công cụ ở trên để bắt đầu.</EmptyState>
        ) : (
          <div className="divide-y divide-border border-y border-border">
            {clients.map((c) => {
              const isOpen = expanded === c.client_id;
              const detail = details[c.client_id];
              const step = wizardStep(c);
              return (
                <div key={c.client_id}>
                  <button type="button" className="flex w-full items-center gap-3 py-3 text-left hover:bg-surface-2 md:gap-5"
                          onClick={() => toggleExpand(c)} aria-expanded={isOpen}>
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5 md:flex-row md:items-center md:gap-3">
                      <span className="font-medium">{c.tool_name}</span>
                      <Badge variant="secondary" className="w-fit text-xs">{CLIENT_KIND_LABEL[c.client_kind] ?? c.client_kind}</Badge>
                    </span>
                    <Badge variant={c.status === "active" ? "success" : c.status === "suspended" ? "destructive" : "warning"}>
                      {c.status === "active" ? "Đã kích hoạt" : c.status === "suspended" ? "Đã khóa" : "Nháp"}
                    </Badge>
                    <span className="text-muted-foreground" aria-hidden>{isOpen ? "▲" : "▼"}</span>
                  </button>

                  {isOpen && (
                    <div className="border-t border-border bg-surface-2 px-4 pb-5 pt-4">
                      <div className="mb-4 flex flex-wrap gap-x-6 gap-y-1">
                        {stepBadge(1, "Tạo nháp", step)}
                        {stepBadge(2, "Cấu hình", step)}
                        {stepBadge(3, "Xem lại + kích hoạt", step)}
                        {stepBadge(4, "Cấp khóa", step)}
                      </div>

                      <p className="mb-4 text-sm text-muted-foreground">{c.purpose}</p>

                      {/* Bước 2 — cấu hình */}
                      <div className={cn(step > 2 && "opacity-70")}>
                        <div className="mb-2 font-semibold">Bước 2 — Cấu hình</div>
                        <fieldset className="mb-3" disabled={isDashAdmin}>
                          <legend className="text-sm font-semibold">Nền tảng</legend>
                          <div className="mt-1 grid gap-x-6 gap-y-1 sm:grid-cols-3">
                            {(platforms.length ? platforms.map((p) => p.key) : cfgPlatforms).map((key) => (
                              <label key={key} className="flex min-h-11 items-center gap-3">
                                <input type="checkbox" className={checkboxCls} checked={cfgPlatforms.includes(key)}
                                       onChange={(e) => setCfgPlatforms((cur) => e.target.checked ? [...cur, key] : cur.filter((x) => x !== key))} />
                                {key}
                              </label>
                            ))}
                          </div>
                        </fieldset>
                        <fieldset disabled={isDashAdmin}>
                          <legend className="text-sm font-semibold">Quyền (scope)</legend>
                          <div className="mt-1 grid gap-x-6 gap-y-1 sm:grid-cols-2">
                            {SHARED_SCOPE_OPTIONS.filter((s) => s !== "jobs:claim:system" || c.client_kind === "fallback_tool").map((sc) => (
                              <label key={sc} className="flex min-h-11 items-center gap-3">
                                <input type="checkbox" className={checkboxCls} checked={cfgScopes.includes(sc)}
                                       onChange={(e) => setCfgScopes((cur) => e.target.checked ? [...cur, sc] : cur.filter((x) => x !== sc))} />
                                <span>{SCOPE_LABEL[sc] ?? sc} <span className="font-mono text-sm text-muted-foreground">({sc})</span></span>
                              </label>
                            ))}
                          </div>
                        </fieldset>
                        {!isDashAdmin && (
                          <Button size="sm" variant="outline" className="mt-3" disabled={cfgSaving} onClick={() => void handleConfigure(c)}>
                            {cfgSaving ? "Đang lưu…" : "Lưu cấu hình"}
                          </Button>
                        )}
                      </div>

                      {/* Bước 3 — kích hoạt */}
                      <div className={cn("mt-5 border-t border-border pt-4", step < 2 && "opacity-50")}>
                        <div className="mb-2 font-semibold">Bước 3 — Xem lại + kích hoạt</div>
                        {c.status === "active" ? (
                          <p className="text-sm text-muted-foreground">
                            Đã kích hoạt lúc {fmtDate(c.activated_at)} bởi <span className="font-mono font-semibold">
                              {c.activated_by_username ?? (c.activated_by ? c.activated_by.slice(0, 8) + "…" : "—")}
                            </span>.
                          </p>
                        ) : step < 3 ? (
                          <p className="text-sm text-muted-foreground">Lưu cấu hình ở bước 2 trước đã.</p>
                        ) : !isDashAdmin && (
                          <div className="flex flex-wrap items-end gap-3">
                            <Field label={`Gõ lại tên Tool để xác nhận: "${c.tool_name}"`} className="min-w-64 flex-1">
                              {(p) => <Input {...p} value={confirmName} onChange={(e) => setConfirmName(e.target.value)} />}
                            </Field>
                            <Button size="sm" disabled={activating || confirmName.trim() !== c.tool_name} onClick={() => void handleActivate(c)}>
                              {activating ? "Đang kích hoạt…" : "Kích hoạt"}
                            </Button>
                          </div>
                        )}
                      </div>

                      {/* Bước 4 — cấp khóa */}
                      <div className={cn("mt-5 border-t border-border pt-4", step < 4 && "opacity-50")}>
                        <div className="mb-2 flex items-center justify-between">
                          <span className="font-semibold">Bước 4 — Cấp khóa</span>
                          {!isDashAdmin && (
                            <Button size="sm" disabled={step < 4 || issuing} onClick={() => void handleIssue(c)}>
                              <Plus aria-hidden /> {issuing ? "Đang tạo…" : "Cấp khóa mới"}
                            </Button>
                          )}
                        </div>
                        {step < 4 ? (
                          <p className="text-sm text-muted-foreground">Kích hoạt ở bước 3 trước đã.</p>
                        ) : !detail?.credentials?.length ? (
                          <p className="text-sm text-muted-foreground">Chưa có khóa nào. Mỗi khóa chỉ bind được một thiết bị — cần thêm máy thì cấp thêm khóa.</p>
                        ) : (
                          <RTable caption="Khóa kết nối" columns={["Khóa (ID)", "Thiết bị đã kích hoạt", "Trạng thái", "Dùng gần nhất", ""]}>
                            {detail.credentials.map((k) => (
                              <RRow key={k.key_id}>
                                <RCell label="Khóa (ID)">
                                  <div className="flex items-center gap-1">
                                    <code className="break-all rounded bg-muted px-1 py-0.5 font-mono text-xs">mpk_{k.key_id}</code>
                                    <button type="button" title="Sao chép key_id" className="shrink-0 text-muted-foreground hover:text-foreground"
                                            onClick={() => { void navigator.clipboard.writeText("mpk_" + k.key_id); toast.success("Đã sao chép prefix khóa"); }}>
                                      <Copy className="size-3.5" aria-hidden />
                                    </button>
                                  </div>
                                  <p className="mt-0.5 text-[10px] text-muted-foreground">Prefix — ghép với secret khi cấp khóa</p>
                                </RCell>
                                <RCell label="Thiết bị" className="text-xs text-muted-foreground">{k.bound_device_id ?? "Chưa kích hoạt"}</RCell>
                                <RCell label="Trạng thái">
                                  <Badge variant={k.status === "active" ? "success" : "destructive"}>{k.status === "active" ? "Hoạt động" : "Đã thu hồi"}</Badge>
                                </RCell>
                                <RCell label="Dùng gần nhất" className="text-muted-foreground">{fmtDate(k.last_used_at)}</RCell>
                                <RCell label="">
                                  {!isDashAdmin && k.status === "active" && (
                                    <Button variant="outline" size="sm" className="border-destructive/60 text-destructive"
                                            onClick={() => setRevokeTarget({ clientId: c.client_id, keyId: k.key_id })}>
                                      Thu hồi
                                    </Button>
                                  )}
                                </RCell>
                              </RRow>
                            ))}
                          </RTable>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Section>

      <SecretOnceDialog secret={secret} onClose={() => setSecret(null)} />

      <ConfirmDialog
        open={!!revokeTarget}
        title="Thu hồi khóa kết nối?"
        description="Khóa bị thu hồi ngay lập tức. Thiết bị đang dùng khóa này sẽ mất quyền truy cập và cần được cấp khóa mới."
        confirmLabel="Thu hồi khóa"
        busy={revoking}
        onConfirm={() => void handleRevoke()}
        onCancel={() => setRevokeTarget(null)}
      />
    </div>
  );
}
