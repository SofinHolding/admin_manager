/**
 * Tích hợp Tool — trang cho tài khoản có `capability:tool_integration`.
 * Toàn bộ nội dung (trạng thái, khóa, endpoint, schema, ví dụ, snapshot gần nhất) lấy từ
 * `GET /v1/integrations/me/connection-guide` — KHÔNG hardcode endpoint hay base URL ở đây.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Database, Download, KeyRound, Laptop, RotateCw, ShieldAlert, Trash2,
} from "lucide-react";
import {
  integrationApi, type ConnectionGuide, type ConnectionGuideEndpoint, type IssuedCredential,
} from "../api";
import { ApiError, errorMessage } from "../lib/errors";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { EmptyState, Notice, PageHeader, Section, TechDetails } from "../components/kit";
import { SecretOnceDialog, type OneTimeSecret } from "../components/SecretOnceDialog";

function fmt(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("vi-VN");
}

const CLIENT_STATUS_LABEL: Record<string, string> = {
  pending_email_verification: "Đang chờ xác thực email",
  active: "Đang hoạt động",
  suspended: "Đã bị thu hồi quyền",
  draft: "Đang khởi tạo",
  deleted: "Đã xóa",
};

function jsonBlock(v: unknown): string {
  return JSON.stringify(v, null, 2);
}

/** `format` mẫu "Bearer mpk_<key_id>.<secret>" — thay `<key_id>`/`<secret>` để tạo ví dụ, secret luôn là placeholder. */
function authHeaderExample(guide: ConnectionGuide): string {
  const keyId = guide.credential?.key_id ?? "<key_id>";
  return guide.auth.format.replace("<key_id>", keyId).replace("<secret>", "<SECRET>");
}

function curlExample(guide: ConnectionGuide, ep: ConnectionGuideEndpoint): string {
  const url = `${guide.base_url ?? "<BASE_URL>"}${ep.path}`;
  const lines = [
    `curl -X ${ep.method} "${url}" \\`,
    `  -H "Authorization: ${authHeaderExample(guide)}" \\`,
    `  -H "X-Contract-Version: ${guide.contract_version}" \\`,
    `  -H "X-Installation-Id: <installation_id>" \\`,
  ];
  if (ep.example_request) {
    lines.push(`  -H "Content-Type: application/json" \\`);
    lines.push(`  -d '${JSON.stringify(ep.example_request)}'`);
  } else {
    lines[lines.length - 1] = lines[lines.length - 1].replace(/ \\$/, "");
  }
  return lines.join("\n");
}

function pythonExample(guide: ConnectionGuide, ep: ConnectionGuideEndpoint): string {
  const url = `${guide.base_url ?? "<BASE_URL>"}${ep.path}`;
  const headers = `{
    "Authorization": "${authHeaderExample(guide)}",
    "X-Contract-Version": "${guide.contract_version}",
    "X-Installation-Id": "<installation_id>",
}`;
  const call = ep.example_request
    ? `client.request("${ep.method}", "${url}", headers=headers, json=${jsonBlock(ep.example_request)})`
    : `client.request("${ep.method}", "${url}", headers=headers)`;
  return `import httpx\n\nheaders = ${headers}\nwith httpx.Client(timeout=30) as client:\n    response = ${call}\n    response.raise_for_status()\n    print(response.json())`;
}

function envSample(guide: ConnectionGuide): string {
  return [
    `BASE_URL=${guide.base_url ?? ""}`,
    `KEY_ID=${guide.credential?.key_id ?? ""}`,
    `CONTRACT_VERSION=${guide.contract_version}`,
    "",
  ].join("\n");
}

function downloadText(filename: string, content: string, type = "text/plain") {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function MyToolsPage() {
  const [guide, setGuide] = useState<ConnectionGuide | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [secret, setSecret] = useState<OneTimeSecret | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setNotFound(false);
    try {
      const g = await integrationApi.connectionGuide();
      setGuide(g);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setNotFound(true);
      } else {
        setError(errorMessage(e, "Không tải được thông tin kết nối. Hãy thử lại."));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const showIssued = (title: string, issued: IssuedCredential) => {
    if (!issued.api_key) return; // reveal_state=pending: admin cấp hộ, chủ phải tự "Nhận khóa"
    setSecret({
      title,
      label: "Khóa kết nối",
      value: issued.api_key,
      details: [
        { label: "key_id", value: issued.key_id },
        { label: "quyền", value: issued.scopes.join(", ") || "—" },
        { label: "hết hạn", value: fmt(issued.expires_at) },
      ],
    });
  };

  const handleIssue = async () => {
    if (!guide) return;
    setBusy("issue");
    try {
      const issued = await integrationApi.issueCredential(guide.client.client_id);
      showIssued("Khóa kết nối mới — sao chép ngay", issued);
      await load();
    } catch (e) {
      setError(errorMessage(e, "Không tạo được khóa kết nối."));
    } finally {
      setBusy(null);
    }
  };

  const handleReveal = async () => {
    setBusy("reveal");
    try {
      const issued = await integrationApi.revealCredential();
      showIssued("Khóa kết nối của bạn — sao chép ngay", issued);
      await load();
    } catch (e) {
      setError(errorMessage(e, "Không nhận được khóa. Hãy tải lại trang."));
    } finally {
      setBusy(null);
    }
  };

  const handleRotate = async () => {
    if (!guide?.credential) return;
    setBusy("rotate");
    try {
      const issued = await integrationApi.rotateCredential(guide.client.client_id, guide.credential.key_id);
      showIssued("Khóa kết nối mới — sao chép ngay", issued);
      await load();
    } catch (e) {
      setError(errorMessage(e, "Không xoay được khóa."));
    } finally {
      setBusy(null);
    }
  };

  const handleRevoke = async () => {
    if (!guide?.credential) return;
    if (!window.confirm("Thu hồi khóa hiện tại? Thiết bị đang dùng khóa này sẽ mất kết nối ngay.")) return;
    setBusy("revoke");
    try {
      await integrationApi.revokeCredential(guide.client.client_id, guide.credential.key_id);
      await load();
    } catch (e) {
      setError(errorMessage(e, "Không thu hồi được khóa."));
    } finally {
      setBusy(null);
    }
  };

  const downloadOpenapi = async () => {
    setBusy("openapi");
    try {
      const spec = await integrationApi.myOpenapi();
      downloadText("openapi.json", jsonBlock(spec), "application/json");
    } catch (e) {
      setError(errorMessage(e, "Không tải được openapi.json."));
    } finally {
      setBusy(null);
    }
  };

  if (loading) {
    return (
      <div>
        <PageHeader title="Tích hợp Tool" description="Kết nối Tool của bạn với Manager qua API." />
        <p role="status" className="py-6 text-sm text-muted-foreground">Đang tải thông tin kết nối…</p>
      </div>
    );
  }

  if (notFound) {
    return (
      <div>
        <PageHeader title="Tích hợp Tool" description="Kết nối Tool của bạn với Manager qua API." />
        <EmptyState icon={KeyRound} title="Tool cá nhân đang được khởi tạo"
          action={<Button variant="outline" onClick={() => void load()}>Tải lại</Button>}>
          Thử tải lại sau ít giây.
        </EmptyState>
      </div>
    );
  }

  if (!guide) {
    return (
      <div>
        <PageHeader title="Tích hợp Tool" description="Kết nối Tool của bạn với Manager qua API." />
        {error && <Notice tone="error" action={<Button variant="outline" onClick={() => void load()}>Thử lại</Button>}>{error}</Notice>}
      </div>
    );
  }

  const { client, credential } = guide;

  return (
    <div>
      <PageHeader
        title="Tích hợp Tool"
        description="Kết nối Tool (ứng dụng chạy trên máy của bạn) với Manager qua API riêng của tài khoản."
      />
      {error && <Notice tone="error" className="mb-6" action={<Button variant="outline" onClick={() => void load()}>Thử lại</Button>}>{error}</Notice>}

      {client.status === "pending_email_verification" && (
        <Notice tone="warning" title="Chưa xác thực email" className="mb-6">
          Bạn cần xác thực email trước khi dùng được khóa kết nối. Kiểm tra hộp thư, hoặc gửi lại thư xác thực từ trang Đăng nhập.
        </Notice>
      )}
      {client.status === "suspended" && (
        <Notice tone="error" title="Quyền tích hợp Tool đã bị thu hồi" className="mb-6">
          Quản trị viên đã tắt quyền này của tài khoản bạn. Khóa kết nối đã bị thu hồi. Liên hệ quản trị viên nếu cần dùng lại.
        </Notice>
      )}

      <Section title="Tổng quan" description="Thông tin cơ bản của Tool cá nhân.">
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
          <dt className="text-muted-foreground">Tên Tool</dt><dd>{client.tool_name}</dd>
          <dt className="text-muted-foreground">Trạng thái</dt>
          <dd>
            <Badge variant={client.status === "active" ? "success" : client.status === "suspended" ? "destructive" : "warning"}>
              {CLIENT_STATUS_LABEL[client.status] ?? client.status}
            </Badge>
          </dd>
          <dt className="text-muted-foreground">Nền tảng</dt><dd>{client.allowed_platforms.join(", ") || "—"}</dd>
          <dt className="text-muted-foreground">Quyền (scope)</dt><dd className="font-mono text-xs">{client.allowed_scopes.join(", ") || "—"}</dd>
        </dl>
      </Section>

      <Section title="Khóa kết nối" description="Khóa dùng để Tool gọi API — chỉ hiện plaintext một lần duy nhất.">
        {!credential ? (
          <div className="flex flex-col items-start gap-3">
            <p className="text-sm text-muted-foreground">Chưa có khóa nào đang hoạt động.</p>
            <Button onClick={() => void handleIssue()} disabled={busy === "issue" || client.status !== "active"}>
              <KeyRound aria-hidden /> {busy === "issue" ? "Đang tạo…" : "Cấp khóa mới"}
            </Button>
          </div>
        ) : (
          <div className="space-y-4">
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
              <dt className="text-muted-foreground">key_id</dt><dd className="font-mono text-xs">{credential.key_id}</dd>
              <dt className="text-muted-foreground">Trạng thái</dt>
              <dd>
                <Badge variant={credential.status === "active" ? "success" : "destructive"}>
                  {credential.status === "active" ? "Hoạt động" : "Đã thu hồi"}
                </Badge>
                {credential.reveal_state === "pending" && <Badge variant="warning" className="ml-2">Chờ nhận</Badge>}
                {credential.reveal_state === "expired" && <Badge variant="destructive" className="ml-2">Hết hạn nhận</Badge>}
              </dd>
              <dt className="text-muted-foreground">Thiết bị</dt>
              <dd className="flex items-center gap-2">
                {credential.bound_device_id ? (
                  <><Laptop className="size-4 text-muted-foreground" aria-hidden />{credential.bound_device_id} — kích hoạt {fmt(credential.activated_at)}</>
                ) : (
                  "Chưa kích hoạt — gọi handshake từ Tool"
                )}
              </dd>
              <dt className="text-muted-foreground">Dùng gần nhất</dt><dd>{fmt(credential.last_used_at)}</dd>
              <dt className="text-muted-foreground">Hết hạn</dt><dd>{fmt(credential.expires_at)}</dd>
            </dl>

            <div className="flex flex-wrap gap-3">
              {credential.reveal_state === "pending" && (
                <Button onClick={() => void handleReveal()} disabled={busy === "reveal"}>
                  <KeyRound aria-hidden /> {busy === "reveal" ? "Đang nhận…" : "Nhận khóa"}
                </Button>
              )}
              {credential.reveal_state === "expired" && (
                <Button onClick={() => void handleRotate()} disabled={busy === "rotate"}>
                  <RotateCw aria-hidden /> {busy === "rotate" ? "Đang cấp…" : "Cấp lại khóa"}
                </Button>
              )}
              {(credential.reveal_state === "revealed" || !credential.reveal_state) && credential.status === "active" && (
                <>
                  <Button variant="outline" onClick={() => void handleRotate()} disabled={busy === "rotate"}>
                    <RotateCw aria-hidden /> {busy === "rotate" ? "Đang xoay…" : "Xoay khóa"}
                  </Button>
                  <Button variant="outline" className="border-destructive/60 text-destructive"
                          onClick={() => void handleRevoke()} disabled={busy === "revoke"}>
                    <Trash2 aria-hidden /> {busy === "revoke" ? "Đang thu hồi…" : "Thu hồi"}
                  </Button>
                </>
              )}
            </div>
          </div>
        )}
      </Section>

      <Section title="Kết nối" description="Thông tin để Tool gọi được API.">
        {guide.base_url === null ? (
          <Notice tone="warning">Quản trị viên chưa cấu hình địa chỉ API công khai.</Notice>
        ) : (
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
            <dt className="text-muted-foreground">Base URL</dt><dd className="font-mono text-xs">{guide.base_url}</dd>
            <dt className="text-muted-foreground">Phiên bản hợp đồng</dt><dd className="font-mono text-xs">{guide.contract_version}</dd>
            <dt className="text-muted-foreground">Xác thực</dt>
            <dd className="font-mono text-xs">{guide.auth.header}: {authHeaderExample(guide)}</dd>
          </dl>
        )}
        {guide.required_headers.length > 0 && (
          <table className="mt-4 w-full border-collapse text-sm">
            <thead><tr className="border-b border-border text-left text-muted-foreground">
              <th className="py-1.5 pr-3">Header</th><th className="py-1.5 pr-3">Khi nào bắt buộc</th><th className="py-1.5">Ý nghĩa</th>
            </tr></thead>
            <tbody>
              {guide.required_headers.map((h) => (
                <tr key={h.name} className="border-b border-border last:border-b-0">
                  <td className="py-1.5 pr-3 font-mono text-xs">{h.name}</td>
                  <td className="py-1.5 pr-3 text-muted-foreground">{h.required_when}</td>
                  <td className="py-1.5">{h.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="Các bước tích hợp">
        <ol className="list-decimal space-y-2 pl-6 text-sm">
          <li>Nhận khóa kết nối ở mục &ldquo;Khóa kết nối&rdquo; phía trên.</li>
          <li>Gọi <code className="font-mono text-xs">handshake</code> từ Tool để kích hoạt khóa cho đúng một thiết bị. Đổi máy phải xoay khóa.</li>
          <li>Gửi header <code className="font-mono text-xs">X-Installation-Id</code> nhận được ở mọi request sau đó.</li>
          <li>Tạo snapshot, đẩy dữ liệu theo từng batch, rồi commit.</li>
          <li>Xem kết quả ở mục &ldquo;Dữ liệu đã nhận&rdquo; bên dưới.</li>
        </ol>
      </Section>

      <Section
        title="Endpoint"
        description="Chỉ liệt kê endpoint mà khóa hiện có được phép gọi."
        actions={
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => void downloadOpenapi()} disabled={busy === "openapi"}>
              <Download aria-hidden /> openapi.json
            </Button>
            <Button variant="outline" size="sm" onClick={() => downloadText(".env", envSample(guide))}>
              <Download aria-hidden /> .env mẫu
            </Button>
          </div>
        }
      >
        {guide.endpoints.length === 0 ? (
          <p className="text-sm text-muted-foreground">Chưa có endpoint nào — cấp khóa trước để xem danh sách.</p>
        ) : (
          <div className="space-y-3">
            {guide.endpoints.map((ep) => (
              <TechDetails key={`${ep.method} ${ep.path}`} summary={`${ep.method} ${ep.path} — ${ep.summary}`}>
                <p className="text-muted-foreground">{ep.description}</p>
                <p className="mt-2 font-mono text-xs">scope: {ep.scope}</p>
                {ep.request_schema && (
                  <div className="mt-3">
                    <div className="mb-1 font-semibold">Schema request</div>
                    <pre className="overflow-x-auto rounded bg-surface-2 p-3 text-xs"><code>{jsonBlock(ep.request_schema)}</code></pre>
                  </div>
                )}
                {ep.example_request && (
                  <div className="mt-3">
                    <div className="mb-1 font-semibold">Ví dụ request</div>
                    <pre className="overflow-x-auto rounded bg-surface-2 p-3 text-xs"><code>{jsonBlock(ep.example_request)}</code></pre>
                  </div>
                )}
                {ep.example_response && (
                  <div className="mt-3">
                    <div className="mb-1 font-semibold">Ví dụ response</div>
                    <pre className="overflow-x-auto rounded bg-surface-2 p-3 text-xs"><code>{jsonBlock(ep.example_response)}</code></pre>
                  </div>
                )}
                {ep.error_codes.length > 0 && (
                  <div className="mt-3">
                    <div className="mb-1 font-semibold">Mã lỗi</div>
                    <ul className="list-disc space-y-0.5 pl-5 text-xs">
                      {ep.error_codes.map((code) => {
                        const info = guide.error_codes.find((e) => e.code === code);
                        return <li key={code}><span className="font-mono">{code}</span>{info ? ` (${info.http_status}) — ${info.meaning}` : ""}</li>;
                      })}
                    </ul>
                  </div>
                )}
                <div className="mt-3">
                  <div className="mb-1 font-semibold">Ví dụ curl</div>
                  <pre className="overflow-x-auto rounded bg-surface-2 p-3 text-xs"><code>{curlExample(guide, ep)}</code></pre>
                </div>
                <div className="mt-3">
                  <div className="mb-1 font-semibold">Ví dụ Python</div>
                  <pre className="overflow-x-auto rounded bg-surface-2 p-3 text-xs"><code>{pythonExample(guide, ep)}</code></pre>
                </div>
              </TechDetails>
            ))}
          </div>
        )}
      </Section>

      <Section title="Giới hạn">
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
          <dt className="text-muted-foreground">Số bản ghi mỗi batch</dt><dd>{guide.limits.batch_max_items}</dd>
          <dt className="text-muted-foreground">Dung lượng body tối đa</dt><dd>{guide.limits.body_max_bytes.toLocaleString("vi-VN")} byte</dd>
        </dl>
      </Section>

      <Section title="Thiết bị" description="Thiết bị đã gọi handshake với khóa của Tool này.">
        {guide.installations.length === 0 ? (
          <p className="text-sm text-muted-foreground">Chưa có thiết bị nào kích hoạt.</p>
        ) : (
          <ul className="divide-y divide-border border-y border-border">
            {guide.installations.map((i) => (
              <li key={i.installation_id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2.5 text-sm">
                <Laptop className="size-4 text-muted-foreground" aria-hidden />
                <span className="font-medium">{i.hostname || i.installation_id}</span>
                <span className="text-muted-foreground">{i.os} · {i.app_version}</span>
                <span className="text-muted-foreground">Thấy lần cuối {fmt(i.last_seen_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Dữ liệu đã nhận" description="5 snapshot gần nhất.">
        {guide.recent_snapshots.length === 0 ? (
          <EmptyState icon={Database} title="Chưa có dữ liệu nào">Dữ liệu sẽ xuất hiện ở đây sau khi Tool đẩy snapshot đầu tiên lên.</EmptyState>
        ) : (
          <div className="space-y-4">
            {guide.recent_snapshots.map((s) => (
              <div key={s.snapshot_id} className="border-b border-border pb-4 last:border-b-0">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="font-mono text-xs">{s.snapshot_id}</span>
                  <Badge variant={s.status === "committed" ? "success" : s.status === "failed" ? "destructive" : "secondary"}>{s.status}</Badge>
                  <span className="text-sm text-muted-foreground">{s.platform} · {fmt(s.created_at)}</span>
                </div>
                {s.counts && <p className="mt-1 text-xs text-muted-foreground">{Object.entries(s.counts).map(([k, v]) => `${k}: ${v}`).join(", ")}</p>}
                {!!s.rejections?.length && (
                  <TechDetails summary={`${s.rejections.length} bản ghi bị từ chối`} className="mt-2">
                    <ul className="space-y-1 text-xs">
                      {s.rejections.map((r, idx) => (
                        <li key={`${r.item_key}-${idx}`} className="flex items-start gap-2">
                          <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-hidden />
                          <span><span className="font-mono">{r.item_key}</span> — {r.code}: {r.message}</span>
                        </li>
                      ))}
                    </ul>
                  </TechDetails>
                )}
              </div>
            ))}
          </div>
        )}
      </Section>

      <SecretOnceDialog secret={secret} onClose={() => setSecret(null)} />
    </div>
  );
}
