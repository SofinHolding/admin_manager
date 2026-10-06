/**
 * Công cụ đẩy dữ liệu — quản lý Integration Client & Credential (mặt phẳng USER, token user RS256).
 * Hợp đồng: docs/api/integration-api-v1.md §1–§2. Thiết kế: docs/architecture/ui-design-v1.md.
 *
 * Từ ngữ trên giao diện: client = "công cụ", credential = "khóa kết nối", scope = "quyền của khóa".
 * Thuật ngữ gốc (client_id, key_id, scope) chỉ nằm trong "Chi tiết kỹ thuật".
 *
 *  - Người dùng thường: công cụ của MÌNH; admin: tất cả (lọc theo chủ) + đổi trạng thái công cụ.
 *  - Khóa: tạo / xoay (grace_seconds) / thu hồi. Server chỉ cho CHỦ công cụ hoặc admin xoay/thu hồi;
 *    dash_admin chỉ xem -> nút bị vô hiệu kèm lời giải thích.
 *  - `api_key` chỉ hiện MỘT lần trong SecretOnceDialog, không lưu ở state nào khác, xóa khi đóng hộp thoại.
 *  - Trạng thái provisioning của công cụ đăng ký kèm: GET /v1/accounts/me/integration (pending|active|failed).
 *  - Máy dùng API key `mpk_...` (machine plane) — trang này KHÔNG bao giờ dùng API key để gọi API.
 */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import {
  CheckCircle2, ChevronDown, ChevronRight, CircleSlash, Clock, KeyRound, PauseCircle, Plus, RefreshCw, RotateCw, Trash2, XCircle,
} from "lucide-react";
import {
  accountsApi, adminApi, integrationApi,
  type Account, type ClientKind, type IntegrationClient, type IntegrationCredential, type MyIntegrationStatus,
} from "../api";
import { ApiError, errorMessage, isAbortError } from "../lib/errors";
import { useAuth } from "../auth";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../components/ui/dialog";
import { EmptyState, Field, Notice, PageHeader, Section, TechDetails } from "../components/kit";
import { SecretOnceDialog, type OneTimeSecret } from "../components/SecretOnceDialog";
import { cn } from "../lib/utils";
import { parseList } from "./adminKeyForm";

/* ── Nhãn tiếng Việt ─────────────────────────────────────────────────────────── */

const KIND_LABEL: Record<string, string> = {
  primary_tool: "Công cụ chính", fallback_tool: "Công cụ dự phòng", service: "Dịch vụ",
};

const KIND_HINT: Record<string, string> = {
  primary_tool: "Công cụ bạn dùng hằng ngày để đẩy số liệu về Manager.",
  fallback_tool: "Chạy thay khi công cụ chính không chạy được. Quản trị viên sẽ duyệt quyền nhận việc.",
  service: "Dịch vụ chạy tự động, không có người thao tác.",
};

/** Gợi ý nhanh; API không có danh sách cố định nên người dùng vẫn gõ được nền tảng khác. */
const PLATFORM_SUGGESTIONS: { value: string; label: string }[] = [
  { value: "facebook", label: "Facebook" },
  { value: "youtube", label: "YouTube" },
];

const PLATFORM_LABEL: Record<string, string> = { facebook: "Facebook", youtube: "YouTube" };
const platformLabel = (p: string) => PLATFORM_LABEL[p] ?? p;

/** Quyền của khóa nói bằng lời; scope lạ giữ nguyên chữ gốc. */
const SCOPE_LABEL: Record<string, string> = {
  "integration:handshake": "Kết nối thử",
  "resources:write": "Gửi danh sách kênh",
  "snapshots:write": "Gửi bản chụp số liệu",
  "metrics:write": "Gửi số liệu",
  "jobs:claim": "Nhận việc",
  "jobs:complete": "Báo hoàn thành việc",
};
const scopeText = (scopes?: string[]) => (scopes?.length ? scopes.map((s) => SCOPE_LABEL[s] ?? s).join(", ") : "Chưa có quyền nào");

function fmt(iso?: string | null, empty = "—") {
  if (!iso) return empty;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? empty : d.toLocaleString("vi-VN");
}

/* ── Trạng thái: luôn có biểu tượng + chữ ────────────────────────────────────── */

type Tone = "good" | "warn" | "bad" | "neutral";

const TONE_CLS: Record<Tone, string> = {
  good: "bg-success/12 text-success",
  warn: "bg-warning/15 text-warning",
  bad: "bg-destructive/15 text-destructive",
  neutral: "bg-surface-2 text-muted-foreground",
};

interface StatusInfo { label: string; tone: Tone; icon: typeof CheckCircle2 }

function clientStatus(s: string): StatusInfo {
  switch (s) {
    case "active": return { label: "Đang hoạt động", tone: "good", icon: CheckCircle2 };
    case "pending_email_verification": return { label: "Chờ xác thực email", tone: "warn", icon: Clock };
    case "suspended": return { label: "Đã tạm khóa", tone: "bad", icon: PauseCircle };
    case "deleted": return { label: "Đã xóa", tone: "bad", icon: CircleSlash };
    case "failed": return { label: "Khởi tạo lỗi", tone: "bad", icon: XCircle };
    default: return { label: s, tone: "neutral", icon: Clock };
  }
}

function credentialStatus(k: IntegrationCredential): StatusInfo {
  const expired = k.status === "active" && !!k.expires_at && new Date(k.expires_at).getTime() < Date.now();
  if (expired || k.status === "expired") return { label: "Đã hết hạn", tone: "warn", icon: Clock };
  switch (k.status) {
    case "active": return { label: "Đang hoạt động", tone: "good", icon: CheckCircle2 };
    case "revoked": return { label: "Đã thu hồi", tone: "bad", icon: XCircle };
    default: return { label: k.status, tone: "neutral", icon: Clock };
  }
}

function StatusBadge({ info }: { info: StatusInfo }) {
  const Icon = info.icon;
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-0.5 text-[0.8125rem] font-medium", TONE_CLS[info.tone])}>
      <Icon className="size-3.5" aria-hidden />
      {info.label}
    </span>
  );
}

const selectCls =
  "h-11 w-full rounded-md border border-border-strong bg-surface-1 px-3 text-base transition-colors hover:border-primary/50 md:h-10 md:text-[0.9375rem]";

const NO_RIGHT_TEXT = "Chỉ chủ công cụ hoặc quản trị viên mới tạo, xoay hay thu hồi được khóa. Bạn đang ở chế độ chỉ xem.";

/** 403 của thao tác quản lý khóa: nói bằng lời dễ hiểu, không phản chiếu mã lỗi. */
function manageError(e: unknown, fallback: string) {
  if (e instanceof ApiError && e.status === 403) return NO_RIGHT_TEXT;
  return errorMessage(e, fallback);
}

interface PendingConfirm {
  title: string;
  body: string;
  confirmLabel: string;
  run: () => void;
}

export default function IntegrationClientsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const isDashAdmin = user?.role === "dash_admin";

  const [clients, setClients] = useState<IntegrationClient[]>([]);
  const [loading, setLoading] = useState(true);
  const [owner, setOwner] = useState("");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [prov, setProv] = useState<MyIntegrationStatus | null>(null);

  const [toolName, setToolName] = useState("");
  const [kind, setKind] = useState<ClientKind>("primary_tool");
  const [platforms, setPlatforms] = useState("");
  const [creating, setCreating] = useState(false);
  const [formErr, setFormErr] = useState<{ name?: string; platforms?: string }>({});

  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<IntegrationClient | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [grace, setGrace] = useState("0");
  const [confirmAct, setConfirmAct] = useState<PendingConfirm | null>(null);

  // Khóa vừa tạo: CHỈ nằm ở state này cho tới khi đóng hộp thoại.
  const [issued, setIssued] = useState<OneTimeSecret | null>(null);

  const canManage = (c: IntegrationClient) =>
    isAdmin || (c.owner_account_id ? c.owner_account_id === user?.id : !isDashAdmin);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const d = await integrationApi.listClients(isAdmin ? owner : "", { signal });
      setClients(d.items ?? []);
    } catch (e) {
      if (!isAbortError(e)) toast.error(errorMessage(e, "Không tải được danh sách công cụ. Kiểm tra kết nối mạng rồi bấm Tải lại."));
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
      .catch(() => setProv(null)); // 404: tài khoản không đăng ký kèm công cụ
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
      toast.error(errorMessage(e, "Không tải được chi tiết công cụ"));
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

  /* Nền tảng: ô văn bản + nút gợi ý bật/tắt */
  const platformList = parseList(platforms);
  const togglePlatform = (value: string) => {
    const next = platformList.includes(value) ? platformList.filter((p) => p !== value) : [...platformList, value];
    setPlatforms(next.join(", "));
  };

  const handleCreate = async (ev?: FormEvent) => {
    ev?.preventDefault();
    const plats = parseList(platforms);
    const err: { name?: string; platforms?: string } = {};
    if (!toolName.trim()) err.name = "Hãy nhập tên công cụ để bạn nhận ra nó sau này.";
    if (!plats.length) err.platforms = "Chọn hoặc nhập ít nhất một nền tảng.";
    setFormErr(err);
    if (err.name || err.platforms) return;
    setCreating(true);
    try {
      const c = await integrationApi.createClient({ tool_name: toolName.trim(), client_kind: kind, platforms: plats });
      toast.success("Đã tạo công cụ");
      setToolName(""); setPlatforms("");
      await load();
      setOpenId(c.client_id);
      void loadDetail(c.client_id);
    } catch (e) {
      toast.error(errorMessage(e, "Không tạo được công cụ. Kiểm tra thông tin rồi thử lại."));
    } finally {
      setCreating(false);
    }
  };

  const showIssued = (title: string, res: { api_key: string; key_id: string; scopes: string[]; expires_at?: string | null }) => {
    setIssued({
      title,
      label: "Khóa kết nối (chỉ hiện một lần)",
      value: res.api_key,
      details: [
        { label: "Mã khóa", value: res.key_id },
        { label: "Quyền của khóa", value: scopeText(res.scopes) },
        ...(res.expires_at ? [{ label: "Hết hạn", value: fmt(res.expires_at) }] : []),
      ],
    });
  };

  const issue = async (clientId: string) => {
    setBusy("issue");
    try {
      const res = await integrationApi.issueCredential(clientId);
      showIssued("Đã tạo khóa kết nối", res);
      await loadDetail(clientId);
    } catch (e) {
      toast.error(manageError(e, "Không tạo được khóa kết nối. Thử lại sau ít phút."));
    } finally {
      setBusy(null);
    }
  };

  const askRotate = (clientId: string, keyId: string) => {
    const g = Number(grace);
    if (!Number.isInteger(g) || g < 0 || g > 86400) {
      toast.error("Thời gian giữ khóa cũ phải là số nguyên từ 0 đến 86400 giây");
      return;
    }
    setConfirmAct({
      title: `Xoay khóa ${keyId}?`,
      body: `Hệ thống sẽ tạo khóa mới và chỉ hiện một lần. Công cụ đang dùng khóa cũ sẽ ngừng gửi được dữ liệu ${
        g === 0 ? "ngay lập tức" : `sau ${g} giây`
      }. Hãy dán khóa mới vào công cụ trước thời điểm đó.`,
      confirmLabel: "Xoay khóa",
      run: () => void rotate(clientId, keyId, g),
    });
  };

  const rotate = async (clientId: string, keyId: string, g: number) => {
    setBusy(`rot:${keyId}`);
    try {
      const res = await integrationApi.rotateCredential(clientId, keyId, g);
      showIssued("Đã xoay khóa kết nối", res);
      await loadDetail(clientId);
    } catch (e) {
      toast.error(manageError(e, "Không xoay được khóa. Thử lại sau ít phút."));
    } finally {
      setBusy(null);
    }
  };

  const askRevoke = (clientId: string, keyId: string) => {
    setConfirmAct({
      title: `Thu hồi khóa ${keyId}?`,
      body: "Công cụ đang dùng khóa này sẽ ngừng gửi được dữ liệu ngay lập tức. Không hoàn tác được; muốn kết nối lại bạn phải tạo khóa mới.",
      confirmLabel: "Thu hồi khóa",
      run: () => void revoke(clientId, keyId),
    });
  };

  const revoke = async (clientId: string, keyId: string) => {
    setBusy(`rev:${keyId}`);
    try {
      await integrationApi.revokeCredential(clientId, keyId);
      toast.success("Đã thu hồi khóa");
      await loadDetail(clientId);
    } catch (e) {
      toast.error(manageError(e, "Không thu hồi được khóa. Thử lại sau ít phút."));
    } finally {
      setBusy(null);
    }
  };

  const askSetStatus = (c: IntegrationClient, status: "active" | "suspended") => {
    const suspend = status === "suspended";
    setConfirmAct({
      title: suspend ? `Tạm khóa công cụ ${c.tool_name}?` : `Kích hoạt lại công cụ ${c.tool_name}?`,
      body: suspend
        ? "Mọi khóa kết nối của công cụ này sẽ ngừng dùng được cho tới khi bạn kích hoạt lại."
        : "Công cụ có thể gửi dữ liệu trở lại bằng các khóa còn hiệu lực.",
      confirmLabel: suspend ? "Tạm khóa công cụ" : "Kích hoạt lại",
      run: () => void setClientStatus(c, status),
    });
  };

  const setClientStatus = async (c: IntegrationClient, status: string) => {
    setBusy(`st:${c.client_id}`);
    try {
      await integrationApi.patchClient(c.client_id, { status });
      toast.success(status === "suspended" ? "Đã tạm khóa công cụ" : "Đã kích hoạt lại công cụ");
      await load();
      if (openId === c.client_id) await loadDetail(c.client_id);
    } catch (e) {
      toast.error(manageError(e, "Không cập nhật được trạng thái công cụ"));
    } finally {
      setBusy(null);
    }
  };

  const provState = prov?.provisioning ?? prov?.status;

  return (
    <div>
      <PageHeader
        title="Công cụ đẩy dữ liệu"
        description="Công cụ là phần mềm ngoài (ví dụ công cụ đăng bài) gửi số liệu về Manager. Mỗi công cụ có một khóa kết nối riêng."
      />

      {provState && (
        <div data-testid="provisioning-status" className="mb-6">
          {provState === "pending" && (
            <Notice tone="warning" title="Công cụ đi kèm tài khoản của bạn đang được khởi tạo">
              Bạn cần xác thực email trước. Mở thư Manager đã gửi và bấm vào liên kết trong thư; công cụ sẽ tự sẵn sàng sau đó.
              Chưa thấy thư thì kiểm tra mục thư rác, hoặc nhờ quản trị viên hỗ trợ.
            </Notice>
          )}
          {provState === "active" && (
            <Notice tone="success" title="Công cụ đi kèm tài khoản của bạn đã sẵn sàng">
              Bạn có thể tạo khóa kết nối cho công cụ này ở danh sách bên dưới.
            </Notice>
          )}
          {provState === "failed" && (
            <Notice tone="error" title="Không khởi tạo được công cụ đi kèm tài khoản">
              Hãy liên hệ quản trị viên để được tạo lại.
              {prov?.error?.code && (
                <TechDetails className="mt-2">Mã lỗi: <code>{prov.error.code}</code></TechDetails>
              )}
            </Notice>
          )}
          {provState !== "pending" && provState !== "active" && provState !== "failed" && (
            <Notice tone="info" title="Trạng thái khởi tạo công cụ">{provState}</Notice>
          )}
          {prov?.client_id && (
            <TechDetails className="mt-2">Mã công cụ (client_id): <code className="break-all">{prov.client_id}</code></TechDetails>
          )}
        </div>
      )}

      {isDashAdmin && (
        <Notice tone="info" title="Bạn đang ở chế độ chỉ xem" className="mb-6">
          Quản lý Dashboard xem được công cụ nhưng không xoay hay thu hồi được khóa của người khác. Việc này thuộc về chủ công cụ hoặc quản trị viên.
        </Notice>
      )}

      <Section
        title={isAdmin ? "Tất cả công cụ" : "Công cụ của bạn"}
        description={clients.length ? `${clients.length} công cụ. Bấm vào một công cụ để xem và quản lý khóa kết nối.` : undefined}
        actions={
          <>
            {isAdmin && (
              <select aria-label="Lọc theo chủ sở hữu" className={cn(selectCls, "w-auto")}
                      value={owner} onChange={(e) => setOwner(e.target.value)}>
                <option value="">Mọi chủ sở hữu</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.username}</option>)}
              </select>
            )}
            <Button variant="outline" onClick={() => load()} disabled={loading}>
              <RefreshCw aria-hidden /> {loading ? "Đang tải…" : "Tải lại"}
            </Button>
          </>
        }
      >
        {loading && clients.length === 0 ? (
          <p className="text-muted-foreground">Đang tải…</p>
        ) : clients.length === 0 ? (
          <EmptyState icon={KeyRound} title="Bạn chưa có công cụ nào">
            Để một công cụ gửi được số liệu về Manager, hãy làm ba bước sau.
            <ol className="mt-3 space-y-2 text-foreground">
              <li><strong>Bước 1. Tạo công cụ.</strong> Điền biểu mẫu “Tạo công cụ mới” bên dưới.</li>
              <li><strong>Bước 2. Tạo khóa kết nối.</strong> Mở công cụ vừa tạo rồi bấm “Tạo khóa kết nối”.</li>
              <li><strong>Bước 3. Dán khóa vào công cụ.</strong> Sao chép khóa và dán vào phần cài đặt của công cụ ngoài.</li>
            </ol>
          </EmptyState>
        ) : (
          <ul className="divide-y divide-border border-y border-border">
            {clients.map((c) => {
              const open = openId === c.client_id;
              const panelId = `client-${c.client_id}`;
              return (
                <li key={c.client_id} className="py-2">
                  <button type="button" onClick={() => toggleOpen(c.client_id)}
                          aria-expanded={open} aria-controls={panelId} aria-label={`Chi tiết ${c.tool_name}`}
                          className="flex min-h-11 w-full items-center gap-2 rounded-md px-1 py-2 text-left hover:bg-elev-2">
                    {open ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                      : <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />}
                    <span className="min-w-0 flex-1 break-words font-semibold">{c.tool_name}</span>
                    <StatusBadge info={clientStatus(c.status)} />
                  </button>
                  <dl className="ml-8 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 pb-1 text-sm sm:flex sm:flex-wrap sm:gap-x-6">
                    <div className="contents sm:flex sm:gap-1.5">
                      <dt className="text-muted-foreground">Loại</dt>
                      <dd>{KIND_LABEL[c.client_kind] ?? c.client_kind}</dd>
                    </div>
                    <div className="contents sm:flex sm:gap-1.5">
                      <dt className="text-muted-foreground">Nền tảng</dt>
                      <dd>{c.platforms?.length ? c.platforms.map(platformLabel).join(", ") : "—"}</dd>
                    </div>
                    {isAdmin && (
                      <div className="contents sm:flex sm:gap-1.5">
                        <dt className="text-muted-foreground">Chủ sở hữu</dt>
                        <dd>{c.owner_username ?? c.owner_account_id ?? "—"}</dd>
                      </div>
                    )}
                    <div className="contents sm:flex sm:gap-1.5">
                      <dt className="text-muted-foreground">Tạo lúc</dt>
                      <dd>{fmt(c.created_at)}</dd>
                    </div>
                  </dl>

                  {open && (
                    <div id={panelId} className="ml-8 mt-3 space-y-4 border-l border-border pb-3 pl-4">
                      <ClientPanel
                        client={c}
                        detail={detail}
                        detailLoading={detailLoading}
                        busy={busy}
                        isAdmin={isAdmin}
                        allowed={canManage(c)}
                        grace={grace}
                        onGrace={setGrace}
                        onIssue={() => issue(c.client_id)}
                        onRotate={(k) => askRotate(c.client_id, k)}
                        onRevoke={(k) => askRevoke(c.client_id, k)}
                        onStatus={(s) => askSetStatus(c, s)}
                      />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      <Section title="Tạo công cụ mới" description="Mỗi công cụ ngoài cần một mục riêng. Tạo xong, bạn sẽ tạo khóa kết nối cho nó.">
        <form onSubmit={handleCreate} noValidate className="max-w-xl space-y-4">
          <Field label="Tên công cụ" error={formErr.name} hint="Đặt tên dễ nhớ, ví dụ “Công cụ đăng bài”.">
            {(p) => <Input {...p} value={toolName} maxLength={100} onChange={(e) => setToolName(e.target.value)} placeholder="Ví dụ: Công cụ đăng bài" />}
          </Field>
          <Field label="Loại" hint={KIND_HINT[kind]}>
            {(p) => (
              <select {...p} className={selectCls} value={kind} onChange={(e) => setKind(e.target.value as ClientKind)}>
                <option value="primary_tool">Công cụ chính</option>
                <option value="fallback_tool">Công cụ dự phòng</option>
                <option value="service">Dịch vụ</option>
              </select>
            )}
          </Field>
          <Field label="Nền tảng được phép" error={formErr.platforms}
                 hint="Công cụ chỉ được gửi số liệu của các nền tảng này. Bấm gợi ý hoặc gõ tên, cách nhau bằng dấu phẩy.">
            {(p) => (
              <>
                <Input {...p} value={platforms} onChange={(e) => setPlatforms(e.target.value)} placeholder="facebook, youtube" />
                <div className="flex flex-wrap gap-2 pt-1" role="group" aria-label="Gợi ý nền tảng">
                  {PLATFORM_SUGGESTIONS.map((s) => {
                    const on = platformList.includes(s.value);
                    return (
                      <Button key={s.value} type="button" size="sm" variant={on ? "default" : "outline"}
                              aria-pressed={on} onClick={() => togglePlatform(s.value)}
                              className="h-11 md:h-10">
                        {on && <CheckCircle2 aria-hidden />}{s.label}
                      </Button>
                    );
                  })}
                </div>
              </>
            )}
          </Field>
          <p className="text-sm text-muted-foreground">
            Quyền của công cụ được cắt theo chính sách của tài khoản bạn. Chỉ tạo được khóa kết nối khi email đã xác thực.
          </p>
          <Button type="submit" disabled={creating}>
            <Plus aria-hidden /> Tạo công cụ
          </Button>
        </form>
      </Section>

      <ConfirmDialog action={confirmAct} onCancel={() => setConfirmAct(null)}
                     onConfirm={() => { const a = confirmAct; setConfirmAct(null); a?.run(); }} />
      <SecretOnceDialog secret={issued} onClose={() => setIssued(null)} />
    </div>
  );
}

/* ── Xác nhận thao tác có hậu quả ────────────────────────────────────────────── */

function ConfirmDialog({ action, onCancel, onConfirm }: {
  action: PendingConfirm | null; onCancel: () => void; onConfirm: () => void;
}) {
  return (
    <Dialog open={!!action} onOpenChange={(o) => { if (!o) onCancel(); }}>
      <DialogContent className="max-w-md">
        <DialogTitle>{action?.title}</DialogTitle>
        <DialogDescription className="reading mt-2 text-[0.9375rem]">{action?.body}</DialogDescription>
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <Button variant="outline" onClick={onCancel}>Hủy, giữ nguyên</Button>
          <Button variant="destructive" onClick={onConfirm}>{action?.confirmLabel}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ── Bảng điều khiển của một công cụ ─────────────────────────────────────────── */

function ClientPanel({
  client, detail, detailLoading, busy, isAdmin, allowed, grace, onGrace, onIssue, onRotate, onRevoke, onStatus,
}: {
  client: IntegrationClient;
  detail: IntegrationClient | null;
  detailLoading: boolean;
  busy: string | null;
  isAdmin: boolean;
  allowed: boolean;
  grace: string;
  onGrace: (v: string) => void;
  onIssue: () => void;
  onRotate: (keyId: string) => void;
  onRevoke: (keyId: string) => void;
  onStatus: (s: "active" | "suspended") => void;
}) {
  const blockedByStatus = client.status !== "active";
  const issueBlocked = !allowed || blockedByStatus || detailLoading;
  return (
    <>
      {!allowed && <p className="text-sm text-muted-foreground" data-testid="no-manage-note">{NO_RIGHT_TEXT}</p>}
      {allowed && client.status === "pending_email_verification" && (
        <p className="text-sm text-muted-foreground">Công cụ này chờ xác thực email. Xác thực xong bạn mới tạo được khóa kết nối.</p>
      )}
      {allowed && client.status === "suspended" && (
        <p className="text-sm text-muted-foreground">Công cụ đang bị tạm khóa nên chưa tạo hay dùng được khóa. Hãy liên hệ quản trị viên.</p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={onIssue} disabled={issueBlocked || busy === "issue"}>
          <KeyRound aria-hidden /> Tạo khóa kết nối
        </Button>
        {isAdmin && (
          client.status === "suspended"
            ? <Button variant="outline" onClick={() => onStatus("active")} disabled={busy === `st:${client.client_id}`}>Kích hoạt lại</Button>
            : <Button variant="outline" onClick={() => onStatus("suspended")} disabled={busy === `st:${client.client_id}`}>Tạm khóa công cụ</Button>
        )}
      </div>

      <Field label="Thời gian giữ khóa cũ khi xoay (giây)" className="max-w-xs"
             hint="Điền 0 để khóa cũ ngừng ngay. Điền số lớn hơn nếu bạn cần thời gian dán khóa mới vào công cụ.">
        {(p) => (
          <Input {...p} type="number" min={0} max={86400} inputMode="numeric" value={grace} onChange={(e) => onGrace(e.target.value)} disabled={!allowed} />
        )}
      </Field>

      <div>
        <h3 className="mb-2">Khóa kết nối</h3>
        {detailLoading && !detail ? (
          <p className="text-sm text-muted-foreground">Đang tải khóa kết nối…</p>
        ) : (
          <CredentialList client={detail ?? client} busy={busy} allowed={allowed} onRotate={onRotate} onRevoke={onRevoke} />
        )}
      </div>

      <TechDetails>
        <p>Mã công cụ (client_id): <code className="break-all">{client.client_id}</code></p>
      </TechDetails>
    </>
  );
}

function CredentialList({ client, busy, allowed, onRotate, onRevoke }: {
  client: IntegrationClient; busy: string | null; allowed: boolean;
  onRotate: (keyId: string) => void; onRevoke: (keyId: string) => void;
}) {
  const creds = client.credentials ?? [];
  if (!creds.length) {
    return <p className="text-sm text-muted-foreground">Công cụ này chưa có khóa kết nối. Bấm “Tạo khóa kết nối” để tạo khóa (khóa chỉ hiện một lần).</p>;
  }
  return (
    <ul className="divide-y divide-border border-y border-border">
      {creds.map((k) => (
        <li key={k.key_id} className="space-y-2 py-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="text-sm text-muted-foreground">Mã khóa</span>
            <code className="font-mono text-sm">{k.key_id}</code>
            <StatusBadge info={credentialStatus(k)} />
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
            <dt className="text-muted-foreground">Quyền của khóa</dt>
            <dd>{scopeText(k.scopes)}</dd>
            <dt className="text-muted-foreground">Hết hạn</dt>
            <dd>{fmt(k.expires_at, "Không hết hạn")}</dd>
            <dt className="text-muted-foreground">Dùng lần cuối</dt>
            <dd>{fmt(k.last_used_at, "Chưa dùng")}</dd>
          </dl>
          {k.status === "active" && (
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" aria-label={`Xoay khóa ${k.key_id}`} disabled={!allowed || busy === `rot:${k.key_id}`} onClick={() => onRotate(k.key_id)}>
                <RotateCw aria-hidden /> Xoay khóa
              </Button>
              <Button variant="outline" className="text-destructive" aria-label={`Thu hồi khóa ${k.key_id}`} disabled={!allowed || busy === `rev:${k.key_id}`} onClick={() => onRevoke(k.key_id)}>
                <Trash2 aria-hidden /> Thu hồi khóa
              </Button>
            </div>
          )}
          <TechDetails>
            <p>Quyền gốc (scope): <code className="break-words">{k.scopes?.length ? k.scopes.join(", ") : "—"}</code></p>
          </TechDetails>
        </li>
      ))}
    </ul>
  );
}
