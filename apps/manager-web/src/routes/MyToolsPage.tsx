/**
 * Tool của tôi — trang web-only cho tài khoản có `capability:tool_integration` nhưng KHÔNG có vai trò vào
 * khu quản trị (vd viewer không dashboard). Tối giản: trạng thái Tool đăng ký kèm (nếu có) + trạng thái công cụ/khóa
 * kết nối hiện tại + lối vào trang quản lý đầy đủ /admin/integrations (docs/architecture/capability-model-v1.md §8,
 * mục "Non-goals"; PHASE 4 — docs/architecture/shared-collector-design-v1.md).
 *
 * Đây KHÔNG phải dashboard nhà phát triển đầy đủ: không tạo/xoay/thu hồi khóa ở đây, chỉ xem nhanh rồi trỏ sang
 * /admin/integrations để thao tác (trang đó đã có toàn bộ luồng quản lý).
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { KeyRound } from "lucide-react";
import { accountsApi, integrationApi, type IntegrationClient, type MyIntegrationStatus } from "../api";
import { Button } from "../components/ui/button";
import { EmptyState, Notice, PageHeader, Section } from "../components/kit";

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

/** Lần dùng gần nhất trong số mọi khóa còn/đã có của công cụ (chuỗi ISO nên so sánh từ điển là đủ). */
function lastUsedAt(client: IntegrationClient | null): string | null {
  return (client?.credentials ?? []).reduce<string | null>(
    (latest, c) => (c.last_used_at && (!latest || c.last_used_at > latest) ? c.last_used_at : latest), null);
}

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
          try {
            const c = await integrationApi.getClient(s.client_id);
            if (!cancelled) setClient(c);
          } catch {
            // Không xem được chi tiết (vd vừa bị xóa) — chỉ ẩn phần "Công cụ & khóa kết nối", không phải lỗi trang.
          }
        }
      } catch {
        // 404 = chưa đăng ký kèm Tool nào — không phải lỗi, chỉ không có trạng thái để hiện.
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <div>
      <PageHeader title="Tool của tôi"
        description="Tài khoản của bạn được cấp quyền liên kết Tool đẩy dữ liệu. Tạo và quản lý công cụ, khóa kết nối ở trang đầy đủ bên dưới." />
      <Section title="Trạng thái Tool đăng ký kèm">
        {loading ? (
          <p role="status" className="py-6 text-sm text-muted-foreground">Đang tải…</p>
        ) : status ? (
          <Notice tone={status.status === "active" ? "success" : status.status === "failed" ? "error" : "info"}>
            {STATUS_LABEL[status.status ?? ""] ?? status.status}
            {status.error?.message && <span className="mt-1 block">{status.error.message}</span>}
          </Notice>
        ) : (
          <EmptyState icon={KeyRound} title="Chưa có Tool nào được đăng ký kèm">
            Bạn có thể tự tạo công cụ đẩy dữ liệu ở trang quản lý đầy đủ bên dưới.
          </EmptyState>
        )}
      </Section>
      {client && (
        <Section title="Công cụ & khóa kết nối" description="Chỉ xem nhanh — tạo/xoay/thu hồi khóa ở trang quản lý đầy đủ bên dưới.">
          <dl className="grid gap-4 py-4 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">Tên công cụ</dt>
              <dd className="mt-0.5 font-medium">{client.tool_name}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Trạng thái công cụ</dt>
              <dd className="mt-0.5 font-medium">{CLIENT_STATUS_LABEL[client.status] ?? client.status}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Số khóa kết nối</dt>
              <dd className="mt-0.5 font-medium">{client.credentials?.length ?? 0}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Khóa dùng gần nhất</dt>
              <dd className="mt-0.5 font-medium">{fmt(lastUsedAt(client))}</dd>
            </div>
          </dl>
        </Section>
      )}
      <Section title="Quản lý khóa kết nối">
        <p className="mb-3 text-sm text-muted-foreground">Tạo, xoay hoặc thu hồi khóa API cho Tool của bạn.</p>
        <Button asChild>
          <Link to="/integrations">
            <KeyRound aria-hidden /> Quản lý Tool &amp; khóa kết nối
          </Link>
        </Button>
      </Section>
    </div>
  );
}
