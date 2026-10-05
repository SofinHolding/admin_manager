/**
 * Tích hợp Tool — quản lý Integration Client & Credential (mặt phẳng USER, token user RS256).
 * Hợp đồng: docs/api/integration-api-v1.md §1–§2.
 *
 *  - Người dùng thường: client của MÌNH; admin: tất cả (lọc theo owner) + đổi trạng thái client.
 *  - Credential: cấp / xoay (grace_seconds) / thu hồi. `api_key` chỉ hiện MỘT lần trong SecretOnceDialog,
 *    không lưu ở state nào khác, xoá khi đóng modal.
 *  - Trạng thái provisioning của Tool đăng ký kèm: GET /v1/accounts/me/integration (pending|active|failed).
 *  - Máy dùng API key `mpk_...` (machine plane) — trang này KHÔNG bao giờ dùng API key để gọi API.
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Clock, KeyRound, Plus, RefreshCw, RotateCw, Trash2 } from "lucide-react";
import {
  accountsApi, adminApi, integrationApi,
  type Account, type ClientKind, type IntegrationClient, type MyIntegrationStatus,
} from "../api";
import { errorMessage, isAbortError } from "../lib/errors";
import { useAuth } from "../auth";
import { Button } from "../components/ui/button";
import { SecretOnceDialog, type OneTimeSecret } from "../components/SecretOnceDialog";
import { parseList } from "./adminKeyForm";

const KIND_LABEL: Record<string, string> = {
  primary_tool: "Tool chính", fallback_tool: "Tool dự phòng", service: "Service",
};

function fmt(iso?: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("vi-VN");
}

function statusCls(s: string) {
  switch (s) {
    case "active": return "bg-green-500/15 text-green-700";
    case "pending_email_verification": return "bg-yellow-500/15 text-yellow-700";
    case "revoked": case "suspended": case "deleted": case "failed": return "bg-red-500/15 text-red-600";
    default: return "bg-muted text-muted-foreground";
  }
}

const inputCls = "h-9 w-full rounded-md border border-border bg-elev-1 px-3 text-sm outline-none transition-colors focus:border-primary";

export default function IntegrationClientsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [clients, setClients] = useState<IntegrationClient[]>([]);
  const [loading, setLoading] = useState(true);
  const [owner, setOwner] = useState("");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [prov, setProv] = useState<MyIntegrationStatus | null>(null);

  const [toolName, setToolName] = useState("");
  const [kind, setKind] = useState<ClientKind>("primary_tool");
  const [platforms, setPlatforms] = useState("");
  const [creating, setCreating] = useState(false);

  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<IntegrationClient | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [grace, setGrace] = useState("0");

  // API key vừa cấp: CHỈ nằm ở state này cho tới khi đóng modal.
  const [issued, setIssued] = useState<OneTimeSecret | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const d = await integrationApi.listClients(isAdmin ? owner : "", { signal });
      setClients(d.items ?? []);
    } catch (e) {
      if (!isAbortError(e)) toast.error(errorMessage(e, "Không tải được danh sách client"));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [isAdmin, owner]);

  useEffect(() => {
    const ctl = new AbortController();
    void load(ctl.signal);
    return () => ctl.abort();
  }, [load]);

  useEffect(() => {
    const ctl = new AbortController();
    accountsApi.myIntegration({ signal: ctl.signal })
      .then(setProv)
      .catch(() => setProv(null)); // 404: tài khoản không đăng ký kèm Tool
    if (isAdmin) {
      adminApi.listUsers({ signal: ctl.signal }).then((d) => setAccounts(d.users)).catch(() => setAccounts([]));
    }
    return () => ctl.abort();
  }, [isAdmin]);

  const loadDetail = useCallback(async (id: string) => {
    setDetailLoading(true);
    try {
      setDetail(await integrationApi.getClient(id));
    } catch (e) {
      toast.error(errorMessage(e, "Không tải được chi tiết client"));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const toggleOpen = (id: string) => {
    if (openId === id) { setOpenId(null); setDetail(null); return; }
    setOpenId(id);
    setDetail(null);
    void loadDetail(id);
  };

  const handleCreate = async () => {
    const plats = parseList(platforms);
    if (!toolName.trim()) { toast.error("Nhập tên Tool"); return; }
    if (!plats.length) { toast.error("Nhập ít nhất một nền tảng"); return; }
    setCreating(true);
    try {
      const c = await integrationApi.createClient({ tool_name: toolName.trim(), client_kind: kind, platforms: plats });
      toast.success("Đã tạo client");
      setToolName(""); setPlatforms("");
      await load();
      setOpenId(c.client_id);
      void loadDetail(c.client_id);
    } catch (e) {
      toast.error(errorMessage(e, "Tạo client thất bại"));
    } finally {
      setCreating(false);
    }
  };

  const showIssued = (title: string, res: { api_key: string; key_id: string; scopes: string[]; expires_at?: string | null }) => {
    setIssued({
      title,
      label: "API key (chỉ hiển thị một lần)",
      value: res.api_key,
      details: [
        { label: "key_id", value: res.key_id },
        { label: "scopes", value: res.scopes.join(", ") || "—" },
        ...(res.expires_at ? [{ label: "hết hạn", value: fmt(res.expires_at) }] : []),
      ],
    });
  };

  const issue = async (clientId: string) => {
    setBusy("issue");
    try {
      const res = await integrationApi.issueCredential(clientId);
      showIssued("Đã cấp credential mới", res);
      await loadDetail(clientId);
    } catch (e) {
      toast.error(errorMessage(e, "Cấp credential thất bại"));
    } finally {
      setBusy(null);
    }
  };

  const rotate = async (clientId: string, keyId: string) => {
    const g = Number(grace);
    if (!Number.isInteger(g) || g < 0 || g > 86400) { toast.error("Thời gian ân hạn phải từ 0 đến 86400 giây"); return; }
    setBusy(`rot:${keyId}`);
    try {
      const res = await integrationApi.rotateCredential(clientId, keyId, g);
      showIssued("Đã xoay credential", res);
      await loadDetail(clientId);
    } catch (e) {
      toast.error(errorMessage(e, "Xoay credential thất bại"));
    } finally {
      setBusy(null);
    }
  };

  const revoke = async (clientId: string, keyId: string) => {
    if (!confirm(`Thu hồi credential ${keyId}? Tool đang dùng key này sẽ bị từ chối ngay (AUTH_REVOKED).`)) return;
    setBusy(`rev:${keyId}`);
    try {
      await integrationApi.revokeCredential(clientId, keyId);
      toast.success("Đã thu hồi credential");
      await loadDetail(clientId);
    } catch (e) {
      toast.error(errorMessage(e, "Thu hồi thất bại"));
    } finally {
      setBusy(null);
    }
  };

  const setClientStatus = async (c: IntegrationClient, status: string) => {
    setBusy(`st:${c.client_id}`);
    try {
      await integrationApi.patchClient(c.client_id, { status });
      toast.success("Đã cập nhật trạng thái client");
      await load();
      if (openId === c.client_id) await loadDetail(c.client_id);
    } catch (e) {
      toast.error(errorMessage(e, "Cập nhật thất bại"));
    } finally {
      setBusy(null);
    }
  };

  const provState = prov?.provisioning ?? prov?.status;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Tích hợp Tool</h1>
        <p className="text-sm text-muted-foreground">
          Client và credential để Tool đẩy dữ liệu lên Manager. Mỗi Tool dùng <code>client_id</code> + API key riêng
          (không dùng mật khẩu hay token đăng nhập).
        </p>
      </div>

      {provState && (
        <div data-testid="provisioning-status" role="status"
             className={`flex items-start gap-2 rounded-xl border px-4 py-3 text-sm ${
               provState === "failed" ? "border-destructive/30 bg-destructive/10 text-destructive"
               : provState === "active" ? "border-green-500/30 bg-green-500/10 text-green-700"
               : "border-yellow-500/30 bg-yellow-500/10 text-yellow-700"}`}>
          {provState === "failed" ? <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            : provState === "active" ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
            : <Clock className="mt-0.5 size-4 shrink-0" />}
          <div>
            {provState === "pending" && "Tool đăng ký kèm tài khoản đang được khởi tạo (cần xác thực email)."}
            {provState === "active" && "Tool đăng ký kèm tài khoản đã sẵn sàng — có thể cấp credential."}
            {provState === "failed" && (
              <>Khởi tạo Tool đăng ký kèm thất bại{prov?.error?.code ? ` (${prov.error.code})` : ""}. Liên hệ quản trị viên.</>
            )}
            {provState !== "pending" && provState !== "active" && provState !== "failed" && `Trạng thái khởi tạo Tool: ${provState}`}
            {prov?.client_id && <span className="ml-1 font-mono text-xs opacity-70">{prov.client_id}</span>}
          </div>
        </div>
      )}

      {/* Tạo client */}
      <div className="rounded-xl border border-border bg-elev-1 p-4">
        <h3 className="mb-3 text-sm font-semibold">Tạo client mới</h3>
        <div className="grid gap-3 sm:grid-cols-4">
          <div className="sm:col-span-1">
            <label htmlFor="ic-name" className="mb-1 block text-xs text-muted-foreground">Tên Tool</label>
            <input id="ic-name" className={inputCls} value={toolName} maxLength={100}
                   onChange={(e) => setToolName(e.target.value)} placeholder="vd: Tool đăng bài" />
          </div>
          <div>
            <label htmlFor="ic-kind" className="mb-1 block text-xs text-muted-foreground">Loại</label>
            <select id="ic-kind" className={inputCls} value={kind} onChange={(e) => setKind(e.target.value as ClientKind)}>
              <option value="primary_tool">Tool chính</option>
              <option value="fallback_tool">Tool dự phòng</option>
              <option value="service">Service</option>
            </select>
          </div>
          <div className="sm:col-span-1">
            <label htmlFor="ic-plat" className="mb-1 block text-xs text-muted-foreground">Nền tảng (cách nhau bằng dấu phẩy)</label>
            <input id="ic-plat" className={inputCls} value={platforms}
                   onChange={(e) => setPlatforms(e.target.value)} placeholder="facebook, youtube" />
          </div>
          <div className="flex items-end">
            <Button onClick={handleCreate} disabled={creating} className="h-9 gap-1.5">
              {creating ? <RefreshCw className="size-4 animate-spin" /> : <Plus className="size-4" />}
              Tạo client
            </Button>
          </div>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Nền tảng/scope được cắt theo chính sách của bạn; credential chỉ cấp được khi email đã xác thực.
        </p>
      </div>

      {/* Danh sách */}
      <div className="rounded-xl border border-border bg-elev-1">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
          <h3 className="text-sm font-semibold">{isAdmin ? "Client (tất cả)" : "Client của bạn"} ({clients.length})</h3>
          <div className="flex items-center gap-2">
            {isAdmin && (
              <select aria-label="Lọc theo chủ sở hữu" className="h-8 rounded-md border border-border bg-elev-1 px-2 text-xs"
                      value={owner} onChange={(e) => setOwner(e.target.value)}>
                <option value="">Mọi chủ sở hữu</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.username}</option>)}
              </select>
            )}
            <Button variant="ghost" size="sm" onClick={() => load()} disabled={loading} aria-label="Tải lại">
              <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
            </Button>
          </div>
        </div>

        {loading && clients.length === 0 ? (
          <div className="p-6 text-center text-sm text-muted-foreground">Đang tải…</div>
        ) : clients.length === 0 ? (
          <div className="p-6 text-center text-sm text-muted-foreground">Chưa có client nào. Tạo client đầu tiên ở trên.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="w-8 px-2 py-2" />
                  <th className="px-4 py-2 font-medium">Tool</th>
                  <th className="px-4 py-2 font-medium">Loại</th>
                  <th className="px-4 py-2 font-medium">Nền tảng</th>
                  {isAdmin && <th className="px-4 py-2 font-medium">Chủ sở hữu</th>}
                  <th className="px-4 py-2 font-medium">Trạng thái</th>
                  <th className="px-4 py-2 font-medium">Tạo lúc</th>
                </tr>
              </thead>
              <tbody>
                {clients.map((c) => {
                  const open = openId === c.client_id;
                  return (
                    <ClientRows key={c.client_id} open={open} colSpan={isAdmin ? 7 : 6}
                      head={
                        <tr className="border-b border-border/50 hover:bg-elev-2/50">
                          <td className="px-2 py-2.5">
                            <button type="button" onClick={() => toggleOpen(c.client_id)}
                                    aria-expanded={open} aria-label={`Chi tiết ${c.tool_name}`}
                                    className="rounded p-1 text-muted-foreground hover:bg-elev-2">
                              {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                            </button>
                          </td>
                          <td className="px-4 py-2.5 font-medium">
                            {c.tool_name}
                            <div className="font-mono text-[10px] text-muted-foreground">{c.client_id}</div>
                          </td>
                          <td className="px-4 py-2.5">{KIND_LABEL[c.client_kind] ?? c.client_kind}</td>
                          <td className="px-4 py-2.5 text-xs">{c.platforms?.join(", ") || "—"}</td>
                          {isAdmin && <td className="px-4 py-2.5 text-xs">{c.owner_username ?? c.owner_account_id ?? "—"}</td>}
                          <td className="px-4 py-2.5">
                            <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${statusCls(c.status)}`}>{c.status}</span>
                          </td>
                          <td className="px-4 py-2.5 text-xs text-muted-foreground">{fmt(c.created_at)}</td>
                        </tr>
                      }
                    >
                      <div className="space-y-3 p-4">
                        <div className="flex flex-wrap items-center gap-2">
                          <Button size="sm" onClick={() => issue(c.client_id)} disabled={busy === "issue" || detailLoading}>
                            {busy === "issue" ? <RefreshCw className="size-4 animate-spin" /> : <KeyRound className="size-4" />}
                            Cấp credential
                          </Button>
                          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                            Ân hạn khi xoay (giây)
                            <input aria-label="Ân hạn khi xoay (giây)" type="number" min={0} max={86400}
                                   className="h-8 w-20 rounded-md border border-border bg-elev-1 px-2 text-xs"
                                   value={grace} onChange={(e) => setGrace(e.target.value)} />
                          </label>
                          {isAdmin && (
                            c.status === "suspended"
                              ? <Button size="sm" variant="outline" onClick={() => setClientStatus(c, "active")} disabled={busy === `st:${c.client_id}`}>Kích hoạt lại</Button>
                              : <Button size="sm" variant="outline" onClick={() => setClientStatus(c, "suspended")} disabled={busy === `st:${c.client_id}`}>Tạm khoá client</Button>
                          )}
                        </div>

                        {detailLoading && !detail ? (
                          <div className="text-sm text-muted-foreground">Đang tải credential…</div>
                        ) : (
                          <CredentialTable
                            client={detail ?? c}
                            busy={busy}
                            onRotate={(k) => rotate(c.client_id, k)}
                            onRevoke={(k) => revoke(c.client_id, k)}
                          />
                        )}
                      </div>
                    </ClientRows>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <SecretOnceDialog secret={issued} onClose={() => setIssued(null)} />
    </div>
  );
}

function ClientRows({ head, open, colSpan, children }: {
  head: React.ReactNode; open: boolean; colSpan: number; children: React.ReactNode;
}) {
  return (
    <>
      {head}
      {open && (
        <tr className="border-b border-border/50 bg-surface-1/40">
          <td colSpan={colSpan}>{children}</td>
        </tr>
      )}
    </>
  );
}

function CredentialTable({ client, busy, onRotate, onRevoke }: {
  client: IntegrationClient; busy: string | null;
  onRotate: (keyId: string) => void; onRevoke: (keyId: string) => void;
}) {
  const creds = client.credentials ?? [];
  if (!creds.length) {
    return <div className="text-sm text-muted-foreground">Chưa có credential. Bấm “Cấp credential” để tạo API key (hiển thị một lần).</div>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="px-3 py-2 font-medium">key_id</th>
            <th className="px-3 py-2 font-medium">Scopes</th>
            <th className="px-3 py-2 font-medium">Trạng thái</th>
            <th className="px-3 py-2 font-medium">Hết hạn</th>
            <th className="px-3 py-2 font-medium">Dùng lần cuối</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {creds.map((k) => (
            <tr key={k.key_id} className="border-b border-border/50 last:border-0">
              <td className="px-3 py-2 font-mono text-xs">{k.key_id}</td>
              <td className="px-3 py-2 text-xs">{k.scopes?.join(", ") || "—"}</td>
              <td className="px-3 py-2">
                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${statusCls(k.status)}`}>{k.status}</span>
              </td>
              <td className="px-3 py-2 text-xs text-muted-foreground">{fmt(k.expires_at)}</td>
              <td className="px-3 py-2 text-xs text-muted-foreground">{fmt(k.last_used_at)}</td>
              <td className="px-3 py-2">
                {k.status === "active" && (
                  <div className="flex items-center justify-end gap-1">
                    <Button variant="ghost" size="sm" title="Xoay key" aria-label={`Xoay ${k.key_id}`}
                            disabled={busy === `rot:${k.key_id}`} onClick={() => onRotate(k.key_id)}>
                      <RotateCw className={`size-4 ${busy === `rot:${k.key_id}` ? "animate-spin" : ""}`} />
                    </Button>
                    <Button variant="ghost" size="sm" title="Thu hồi" aria-label={`Thu hồi ${k.key_id}`}
                            className="text-red-500 hover:text-red-600"
                            disabled={busy === `rev:${k.key_id}`} onClick={() => onRevoke(k.key_id)}>
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
