/**
 * Tool của tôi — trang web-only cho tài khoản có `capability:tool_integration` nhưng KHÔNG có vai trò vào
 * khu quản trị (vd viewer không dashboard). Tối giản: trạng thái Tool đăng ký kèm (nếu có) + lối vào trang
 * quản lý đầy đủ /admin/integrations (docs/architecture/capability-model-v1.md §8, mục "Non-goals").
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { KeyRound } from "lucide-react";
import { accountsApi, type MyIntegrationStatus } from "../api";
import { Button } from "../components/ui/button";
import { EmptyState, Notice, PageHeader, Section } from "../components/kit";

const STATUS_LABEL: Record<string, string> = {
  pending_email_verification: "Đang chờ xác thực email",
  pending: "Đang khởi tạo",
  active: "Đã sẵn sàng",
  failed: "Khởi tạo thất bại",
};

export default function MyToolsPage() {
  const [status, setStatus] = useState<MyIntegrationStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await accountsApi.myIntegration();
        if (!cancelled) setStatus(s);
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
      <Section title="Quản lý Tool">
        <Button asChild>
          <Link to="/admin/integrations">
            <KeyRound aria-hidden /> Mở trang quản lý Tool
          </Link>
        </Button>
      </Section>
    </div>
  );
}
