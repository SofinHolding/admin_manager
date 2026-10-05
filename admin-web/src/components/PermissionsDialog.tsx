/**
 * Quản lý permission của MỘT tài khoản (Identity là nguồn sự thật):
 *   xem   GET    /v1/accounts/{id}/permissions
 *   cấp   PUT    /v1/admin/accounts/{id}/permissions/{perm}
 *   thu   DELETE /v1/admin/accounts/{id}/permissions/{perm}
 * Catalog lấy từ GET /v1/admin/permissions (rơi về reward:access / reward:admin nếu không đọc được).
 * Quyền hiệu lực có thể gồm quyền ngầm theo role (admin có tất cả) — UI chỉ cho đổi quyền được cấp tường minh.
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, ShieldCheck } from "lucide-react";
import { permissionsApi, type Account, type PermissionCatalogEntry } from "../api";
import { errorMessage } from "../lib/errors";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";

const FALLBACK_CATALOG: PermissionCatalogEntry[] = [
  { name: "reward:access", description: "Dùng module phân phối điểm Discord" },
  { name: "reward:admin", description: "Quản trị module Reward (job toàn hệ thống, khoá kênh)" },
];

export function PermissionsDialog({ account, onClose }: { account: Account | null; onClose: () => void }) {
  const [catalog, setCatalog] = useState<PermissionCatalogEntry[]>(FALLBACK_CATALOG);
  const [effective, setEffective] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async (id: string, signal?: AbortSignal) => {
    setLoading(true);
    try {
      const d = await permissionsApi.forAccount(id, { signal });
      setEffective(d.permissions ?? []);
    } catch (e) {
      if (!signal?.aborted) toast.error(errorMessage(e, "Không tải được quyền của tài khoản"));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!account) return;
    const ctl = new AbortController();
    setEffective([]);
    void load(account.id, ctl.signal);
    permissionsApi.catalog()
      .then((c) => { if (!ctl.signal.aborted && c.length) setCatalog(c); })
      .catch(() => { /* giữ catalog dự phòng */ });
    return () => ctl.abort();
  }, [account, load]);

  const toggle = async (perm: string, has: boolean) => {
    if (!account) return;
    setBusy(perm);
    try {
      if (has) await permissionsApi.revoke(account.id, perm);
      else await permissionsApi.grant(account.id, perm);
      toast.success(has ? `Đã thu quyền ${perm}` : `Đã cấp quyền ${perm}`);
      await load(account.id);
    } catch (e) {
      toast.error(errorMessage(e, "Thao tác quyền thất bại"));
    } finally {
      setBusy(null);
    }
  };

  const isAdmin = account?.role === "admin";

  return (
    <Dialog open={!!account} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4" /> Quyền (permission) — {account?.username}
        </DialogTitle>
        <DialogDescription>
          Quyền mịn theo domain, cấp/thu qua Identity; hiệu lực ngay ở token kế tiếp (tối đa vài phút) hoặc sau khi đăng nhập lại.
        </DialogDescription>

        {isAdmin && (
          <p className="mt-3 rounded-md border border-border bg-elev-1 px-3 py-2 text-xs text-muted-foreground">
            Tài khoản admin được coi là có mọi quyền quản trị.
          </p>
        )}

        <div className="mt-3 flex flex-col gap-2" aria-busy={loading}>
          {loading && !effective.length ? (
            <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
              <RefreshCw className="size-4 animate-spin" /> Đang tải…
            </div>
          ) : catalog.map((p) => {
            const has = effective.includes(p.name);
            return (
              <div key={p.name} className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
                <div className="min-w-0">
                  <div className="font-mono text-sm">{p.name}</div>
                  {p.description && <div className="text-xs text-muted-foreground">{p.description}</div>}
                </div>
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-medium ${has ? "text-green-600" : "text-muted-foreground"}`}>
                    {has ? "Đang có" : "Chưa có"}
                  </span>
                  <Button size="sm" variant={has ? "outline" : "default"} disabled={busy === p.name || loading}
                          onClick={() => toggle(p.name, has)}
                          aria-label={`${has ? "Thu" : "Cấp"} quyền ${p.name}`}>
                    {busy === p.name && <RefreshCw className="size-3.5 animate-spin" />}
                    {has ? "Thu" : "Cấp"}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}
