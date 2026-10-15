/**
 * Quản lý quyền của MỘT tài khoản (Identity là nguồn sự thật):
 *   xem   GET    /v1/accounts/{id}/permissions
 *   cấp   PUT    /v1/admin/accounts/{id}/permissions/{perm}
 *   thu   DELETE /v1/admin/accounts/{id}/permissions/{perm}
 * Danh mục lấy từ GET /v1/admin/permissions (rơi về reward:access / reward:admin nếu không đọc được).
 * Quyền hiệu lực có thể gồm quyền ngầm theo vai trò (quản trị viên có tất cả) — giao diện chỉ cho đổi quyền được cấp tường minh.
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Check, Minus, ShieldCheck } from "lucide-react";
import { permissionsApi, type Account, type PermissionCatalogEntry } from "../api";
import { adminErrorText, ALL_CAPS, CAP_LABEL } from "../routes/adminKeyForm";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";
import { Notice } from "./kit";

const CAPABILITY_ENTRIES: PermissionCatalogEntry[] = ALL_CAPS.map((name) => ({
  name,
  description: CAP_LABEL[name] ?? name,
}));

const FALLBACK_CATALOG: PermissionCatalogEntry[] = [
  ...CAPABILITY_ENTRIES,
  { name: "reward:access", description: "Dùng phần phân phối điểm Discord" },
  { name: "reward:admin", description: "Quản trị phần phân phối điểm (công việc toàn hệ thống, khóa kênh)" },
];

export function PermissionsDialog({ account, onClose }: { account: Account | null; onClose: () => void }) {
  const [catalog, setCatalog] = useState<PermissionCatalogEntry[]>(FALLBACK_CATALOG);
  const [effective, setEffective] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (id: string, signal?: AbortSignal) => {
    setLoading(true);
    try {
      const d = await permissionsApi.forAccount(id, { signal });
      setEffective(d.permissions ?? []);
      setError(null);
    } catch (e) {
      if (!signal?.aborted) setError(adminErrorText(e, "Không tải được quyền của tài khoản. Hãy đóng hộp thoại và mở lại."));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!account) return;
    const ctl = new AbortController();
    setEffective([]);
    setError(null);
    void load(account.id, ctl.signal);
    permissionsApi.catalog()
      .then((c) => { if (!ctl.signal.aborted && c.length) setCatalog(c); })
      .catch(() => { /* giữ danh mục dự phòng */ });
    return () => ctl.abort();
  }, [account, load]);

  const toggle = async (perm: string, has: boolean) => {
    if (!account) return;
    setBusy(perm);
    setError(null);
    try {
      if (has) await permissionsApi.revoke(account.id, perm);
      else await permissionsApi.grant(account.id, perm);
      toast.success(has ? `Đã thu quyền ${perm}` : `Đã cấp quyền ${perm}`);
      await load(account.id);
    } catch (e) {
      setError(adminErrorText(e, has ? "Không thu được quyền. Hãy thử lại." : "Không cấp được quyền. Hãy thử lại."));
    } finally {
      setBusy(null);
    }
  };

  const isAdmin = account?.role === "admin";

  return (
    <Dialog open={!!account} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-primary-text" aria-hidden /> Quyền của {account?.username}
        </DialogTitle>
        <DialogDescription className="reading mt-1">
          Cấp hoặc thu từng quyền. Thay đổi có hiệu lực ở lần làm mới phiên kế tiếp (tối đa vài phút) hoặc khi người này đăng nhập lại.
        </DialogDescription>

        {isAdmin && (
          <Notice className="mt-4">Quản trị viên luôn có mọi quyền quản trị, không cần cấp thêm.</Notice>
        )}
        {error && <Notice tone="error" className="mt-4">{error}</Notice>}

        {(() => {
          const capabilityItems = catalog.filter((p) => p.name.startsWith("capability:"));
          const rewardItems = catalog.filter((p) => p.name.startsWith("reward:"));
          const otherItems = catalog.filter((p) => !p.name.startsWith("capability:") && !p.name.startsWith("reward:"));
          const groups: { label: string; items: PermissionCatalogEntry[] }[] = [
            { label: "Khả năng (capability)", items: capabilityItems },
            { label: "Thưởng Discord (reward)", items: rewardItems },
            { label: "Khác", items: otherItems },
          ].filter((g) => g.items.length > 0);

          const renderRow = (p: PermissionCatalogEntry) => {
            const has = effective.includes(p.name);
            return (
              <li key={p.name} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3">
                <div className="min-w-0">
                  <div className="font-mono text-sm font-medium">{p.name}</div>
                  {p.description && <div className="text-sm text-muted-foreground">{p.description}</div>}
                  {p.name === "capability:tool_integration" && (
                    <div className="mt-1 text-sm text-muted-foreground">
                      Bật ⇒ hệ thống tự tạo Tool cá nhân + khóa chờ người dùng nhận. Tắt ⇒ Tool cá nhân bị tạm dừng và khóa bị thu hồi.
                    </div>
                  )}
                  <div className={`mt-1 inline-flex items-center gap-1 text-sm font-medium ${has ? "text-success" : "text-muted-foreground"}`}>
                    {has ? <Check className="size-4" aria-hidden /> : <Minus className="size-4" aria-hidden />}
                    {has ? "Đang có" : "Chưa có"}
                  </div>
                </div>
                <Button variant={has ? "outline" : "default"} disabled={busy === p.name || loading}
                        onClick={() => toggle(p.name, has)}
                        aria-label={`${has ? "Thu" : "Cấp"} quyền ${p.name}`}>
                  {busy === p.name ? "Đang lưu…" : has ? "Thu quyền" : "Cấp quyền"}
                </Button>
              </li>
            );
          };

          if (loading && !effective.length) {
            return (
              <ul className="mt-4 divide-y divide-border border-y border-border" aria-busy={loading}>
                <li className="py-4 text-sm text-muted-foreground" role="status">Đang tải quyền…</li>
              </ul>
            );
          }

          return groups.map((g) => (
            <div key={g.label} className="mt-4">
              <div className="text-sm font-semibold text-muted-foreground">{g.label}</div>
              <ul className="mt-1 divide-y divide-border border-y border-border" aria-busy={loading}>
                {g.items.map(renderRow)}
              </ul>
            </div>
          ));
        })()}
        <div className="mt-5 flex justify-end">
          <Button variant="outline" onClick={onClose}>Xong</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
