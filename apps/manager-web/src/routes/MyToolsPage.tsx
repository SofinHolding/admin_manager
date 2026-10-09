/**
 * Tool của tôi — trang cho tài khoản có `capability:tool_integration`.
 * Tab 1: Tổng quan — trạng thái Tool + khóa kết nối + lối vào /integrations
 * Tab 2: Tài liệu API — endpoint, schema, ví dụ Python/TS, cách lấy credential
 * Admin cũng thấy tab này (đều có capability:tool_integration qua migration 0005).
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BookOpen, KeyRound } from "lucide-react";
import { accountsApi, integrationApi, type IntegrationClient, type MyIntegrationStatus } from "../api";
import { Button } from "../components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { EmptyState, Notice, PageHeader, Section, TechDetails } from "../components/kit";

const STATUS_LABEL: Record<string, string> = {
  pending_email_verification: "Đang chờ xác thực email",
  pending: "Đang khởi tạo",
  active: "Đã sẵn sàng",
  failed: "Khởi tạo thất bại",
};

const CLIENT_STATUS_LABEL: Record<string, string> = {
  active: "Đang hoạt động",
  pending_email_verification: "Chờ xác thực email",
  suspended: "Đã tạm khóa",
  deleted: "Đã xóa",
};

function fmt(iso?: string | null) {
  if (!iso) return "Chưa dùng lần nào";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "Chưa dùng lần nào" : d.toLocaleString("vi-VN");
}

function lastUsedAt(client: IntegrationClient | null): string | null {
  return (client?.credentials ?? []).reduce<string | null>(
    (latest, c) => (c.last_used_at && (!latest || c.last_used_at > latest) ? c.last_used_at : latest), null);
}

// ── API Docs Tab ──────────────────────────────────────────────────────────────

const CODE_PYTHON = `import httpx

BASE_URL = "https://your-gateway-url"  # hoặc http://127.0.0.1:PORT trong DEV
API_KEY  = "mpk_..."                   # lấy từ /integrations → Tạo khóa kết nối

client = httpx.AsyncClient(
    base_url=BASE_URL,
    headers={"Authorization": f"Bearer {API_KEY}",
             "X-Contract-Version": "2026-10-01"},
    timeout=15,
)

# 1. Handshake (khai báo tool instance)
await client.post("/v1/integrations/handshake", json={
    "installation": {"device_id": "my-pc-01", "hostname": "my-pc",
                     "os": "windows", "app_version": "1.0.0"},
    "tool":         {"tool_name": "My Tool", "contract_version": "2026-10-01"},
    "platforms":    ["facebook"],
    "capabilities": ["resources.push", "snapshots.push", "metrics.push"],
})

# 2. Đẩy dữ liệu (snapshot đầy đủ)
snap = await client.post("/v1/integrations/snapshots", json={
    "platform":        "facebook",
    "snapshot_type":   "full",
    "observed_at":     "2026-10-09T00:00:00Z",
    "report_timezone": "Asia/Ho_Chi_Minh",
    "expected_counts": {"resources": 1, "content": 2},
})
sid = snap.json()["data"]["snapshot_id"]

await client.post(f"/v1/integrations/snapshots/{sid}/resources", json={
    "items": [{"platform": "facebook", "resource_type": "page",
               "external_id": "123456789", "display_name": "My Page",
               "observed_at": "2026-10-09T00:00:00Z"}]
})
await client.post(f"/v1/integrations/snapshots/{sid}/content", json={
    "items": [{"external_id": "post-1", "resource_external_id": "123456789",
               "published_date": "2026-10-08",
               "metrics": {"views": 1200, "reactions": 35},
               "observed_at": "2026-10-09T00:00:00Z"}]
})
await client.post(f"/v1/integrations/snapshots/{sid}/commit", json={})`;

const CODE_TS = `import { IntegrationClient } from "manager-sdk"; // hoặc tự gọi httpx

const client = new IntegrationClient({
  baseUrl: "https://your-gateway-url",
  apiKey:  "mpk_...",  // lấy từ /integrations → Tạo khóa kết nối
});

// Handshake
await client.handshake({
  deviceId: "my-pc-01", hostname: "my-pc",
  osName: "windows", appVersion: "1.0.0",
  toolName: "My Tool",
  platforms: ["facebook"],
  capabilities: ["resources.push", "snapshots.push", "metrics.push"],
});

// Đẩy snapshot (SDK tự chia batch, tự tính expected_counts, tự commit)
await client.pushSnapshot({
  platform:        "facebook",
  observedAt:      new Date().toISOString(),
  reportTimezone:  "Asia/Ho_Chi_Minh",
  resources: [{ platform: "facebook", resource_type: "page",
                external_id: "123456789", display_name: "My Page",
                observed_at: new Date().toISOString() }],
  content: [{ external_id: "post-1", resource_external_id: "123456789",
              published_date: "2026-10-08",
              metrics: { views: 1200, reactions: 35 },
              observed_at: new Date().toISOString() }],
});`;

interface ApiEndpoint { method: string; path: string; scope?: string; desc: string }
const ENDPOINTS: ApiEndpoint[] = [
  { method: "POST", path: "/v1/integrations/handshake",        scope: "integration:handshake", desc: "Khai báo tool instance (device_id + capabilities). Gọi một lần khi khởi động." },
  { method: "POST", path: "/v1/integrations/snapshots",        scope: "snapshots:write",       desc: "Tạo snapshot mới (status=building). Trả snapshot_id." },
  { method: "POST", path: "/v1/integrations/snapshots/{id}/resources", scope: "snapshots:write", desc: "Gửi batch kênh/page (tối đa 500 bản ghi)." },
  { method: "POST", path: "/v1/integrations/snapshots/{id}/content",   scope: "snapshots:write", desc: "Gửi batch bài đăng + metric (views, reactions, comments…)." },
  { method: "POST", path: "/v1/integrations/snapshots/{id}/metrics",   scope: "metrics:write",   desc: "Gửi batch metric theo ngày của kênh." },
  { method: "POST", path: "/v1/integrations/snapshots/{id}/commit",    scope: "snapshots:write", desc: "Commit snapshot (atomic). Dữ liệu chính thức có hiệu lực sau bước này." },
  { method: "GET",  path: "/v1/integrations/snapshots/{id}",           scope: "snapshots:write", desc: "Kiểm tra trạng thái snapshot." },
  { method: "POST", path: "/v1/integrations/resources/batch",          scope: "resources:write", desc: "Upsert kênh ngoài snapshot (dùng khi không cần commit nguyên tử)." },
];

const METHOD_CLS: Record<string, string> = {
  GET:  "bg-blue-100 text-blue-800",
  POST: "bg-green-100 text-green-800",
  PUT:  "bg-yellow-100 text-yellow-800",
  DELETE: "bg-red-100 text-red-800",
};

function ApiDocsTab() {
  return (
    <div className="space-y-8">
      {/* Auth */}
      <Section title="Xác thực" description="Mọi request đều cần header Authorization.">
        <div className="space-y-2 text-sm">
          <p>Lấy API key từ trang <Link to="/integrations" className="text-primary underline">Khóa kết nối</Link> → Tạo khóa kết nối.</p>
          <pre className="rounded-md bg-muted px-4 py-3 text-xs overflow-x-auto">
{`Authorization: Bearer mpk_<key_id>.<secret>
X-Contract-Version: 2026-10-01
Content-Type: application/json
Idempotency-Key: <unique-id>  # tùy chọn, chống gửi trùng`}
          </pre>
          <p className="text-muted-foreground">Owner của dữ liệu được xác định từ API key — không cần và không được khai báo trong body.</p>
        </div>
      </Section>

      {/* Endpoints */}
      <Section title="Endpoints chính" description="Contract version 2026-10-01. Mọi response có dạng {ok, data, meta}.">
        <div className="divide-y divide-border border border-border rounded-md text-sm">
          {ENDPOINTS.map((e) => (
            <div key={e.path + e.method} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-start sm:gap-4">
              <span className={`shrink-0 rounded px-2 py-0.5 font-mono text-xs font-semibold ${METHOD_CLS[e.method] ?? ""}`}>{e.method}</span>
              <code className="shrink-0 text-xs break-all">{e.path}</code>
              <span className="text-muted-foreground">{e.desc}</span>
              {e.scope && <span className="shrink-0 rounded bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">{e.scope}</span>}
            </div>
          ))}
        </div>
      </Section>

      {/* Rate limits */}
      <Section title="Giới hạn và idempotency">
        <ul className="space-y-1 text-sm text-muted-foreground list-disc pl-5">
          <li>Mỗi batch tối đa <strong>500 bản ghi</strong>, body tối đa <strong>2 MiB</strong> (nén gzip được).</li>
          <li>Header <code>Idempotency-Key</code> (8-128 ký tự): cùng key + cùng body → server trả response cũ, không nhân đôi dữ liệu.</li>
          <li>Metric là <strong>số tuyệt đối</strong> (ghi đè, không cộng dồn). <code>null</code> = chưa thu thập, <code>0</code> = đã thu thập và bằng 0.</li>
          <li>Retry an toàn với lỗi mạng hoặc 5xx. Lỗi 4xx là lỗi của caller, không retry tự động.</li>
        </ul>
      </Section>

      {/* Code examples */}
      <Section title="Ví dụ tích hợp — Python">
        <pre className="rounded-md bg-muted px-4 py-3 text-xs overflow-x-auto whitespace-pre-wrap">{CODE_PYTHON}</pre>
      </Section>

      <Section title="Ví dụ tích hợp — TypeScript / JavaScript">
        <pre className="rounded-md bg-muted px-4 py-3 text-xs overflow-x-auto whitespace-pre-wrap">{CODE_TS}</pre>
      </Section>

      {/* Error codes */}
      <Section title="Mã lỗi thường gặp">
        <div className="divide-y divide-border border border-border rounded-md text-sm">
          {[
            ["AUTH_INVALID / 401", "API key sai định dạng hoặc không tồn tại."],
            ["AUTH_REVOKED / 401", "API key đã bị thu hồi."],
            ["SCOPE_DENIED / 403", "Scope của key không đủ quyền cho thao tác này."],
            ["PLATFORM_NOT_ALLOWED / 403", "Nền tảng không nằm trong danh sách được phép của client."],
            ["SNAPSHOT_NOT_FOUND / 404", "snapshot_id không đúng hoặc không thuộc key này."],
            ["BATCH_TOO_LARGE / 422", "Batch vượt 500 bản ghi."],
            ["RECORD_VALIDATION_FAILED / 422", "Một hoặc nhiều bản ghi trong batch không hợp lệ. Xem trường `rejections`."],
            ["IDEMPOTENCY_CONFLICT / 409", "Cùng Idempotency-Key nhưng khác body — không được phép."],
            ["UNAVAILABLE / 503", "Server tạm thời không khả dụng. Retry với exponential backoff."],
          ].map(([code, desc]) => (
            <div key={code} className="flex gap-4 px-4 py-2">
              <code className="shrink-0 text-xs font-mono text-destructive w-52">{code}</code>
              <span className="text-muted-foreground">{desc}</span>
            </div>
          ))}
        </div>
      </Section>

      <TechDetails summary="OpenAPI spec đầy đủ">
        <p>File <code>docs/api/openapi/integration-service.v1.json</code> trong repo chứa toàn bộ schema. Truy cập <code>/openapi.json</code> trực tiếp từ integration-service khi cần schema mới nhất.</p>
      </TechDetails>
    </div>
  );
}

// ── Trang chính ───────────────────────────────────────────────────────────────

export default function MyToolsPage() {
  const [status, setStatus] = useState<MyIntegrationStatus | null>(null);
  const [client, setClient] = useState<IntegrationClient | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await accountsApi.myIntegration();
        if (cancelled) return;
        setStatus(s);
        if (s.client_id) {
          const c = await integrationApi.getClient(s.client_id);
          if (!cancelled) setClient(c);
        }
      } catch {
        // Không có tool đăng ký kèm: để trống
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <div>
      <PageHeader title="Tool của tôi" description="Quản lý tích hợp Tool và xem tài liệu API." />

      <Tabs defaultValue="overview" className="mt-6">
        <TabsList>
          <TabsTrigger value="overview"><KeyRound className="mr-1.5 size-4" aria-hidden />Tổng quan</TabsTrigger>
          <TabsTrigger value="api-docs"><BookOpen className="mr-1.5 size-4" aria-hidden />Tài liệu API</TabsTrigger>
        </TabsList>

        {/* Tab 1: Tổng quan */}
        <TabsContent value="overview" className="mt-6 space-y-6">
          {loading ? (
            <p className="text-muted-foreground">Đang tải…</p>
          ) : status?.provisioning || status?.status ? (
            <Section title="Tool đăng ký kèm tài khoản">
              {status.provisioning === "pending" && (
                <Notice tone="warning" title="Đang khởi tạo">Xác thực email để kích hoạt tool.</Notice>
              )}
              {status.provisioning === "active" && (
                <Notice tone="success" title="Tool đã sẵn sàng">Tạo khóa kết nối để bắt đầu đẩy dữ liệu.</Notice>
              )}
              {status.provisioning === "failed" && (
                <Notice tone="error" title="Khởi tạo thất bại">Liên hệ quản trị viên.</Notice>
              )}
            </Section>
          ) : (
            <EmptyState icon={KeyRound} title="Chưa có Tool nào">
              <p>Bạn có quyền sử dụng Tool. Tạo Tool và khóa kết nối từ trang bên dưới.</p>
            </EmptyState>
          )}

          {client && (
            <Section title="Công cụ & khóa kết nối" description="Chỉ xem nhanh — quản lý đầy đủ ở trang Khóa kết nối.">
              <dl className="grid gap-4 py-2 text-sm sm:grid-cols-2">
                <div><dt className="text-muted-foreground">Tên công cụ</dt><dd className="mt-0.5 font-medium">{client.tool_name}</dd></div>
                <div><dt className="text-muted-foreground">Trạng thái</dt><dd className="mt-0.5 font-medium">{CLIENT_STATUS_LABEL[client.status] ?? client.status}</dd></div>
                <div><dt className="text-muted-foreground">Số khóa</dt><dd className="mt-0.5 font-medium">{client.credentials?.length ?? 0} khóa</dd></div>
                <div><dt className="text-muted-foreground">Dùng lần cuối</dt><dd className="mt-0.5 font-medium">{fmt(lastUsedAt(client))}</dd></div>
              </dl>
            </Section>
          )}

          <Section title="Quản lý Tool &amp; khóa kết nối">
            <p className="mb-3 text-sm text-muted-foreground">Tạo tool, cấp hoặc thu hồi khóa API (API key chỉ hiện một lần khi tạo).</p>
            <Button asChild>
              <Link to="/integrations"><KeyRound aria-hidden /> Đến trang Khóa kết nối</Link>
            </Button>
          </Section>
        </TabsContent>

        {/* Tab 2: Tài liệu API */}
        <TabsContent value="api-docs" className="mt-6">
          <ApiDocsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
