import { useRef } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Lock } from "lucide-react";
import { Button } from "./ui/button";
import { DialogDescription, DialogOverlay, DialogTitle } from "./ui/dialog";

/**
 * Hộp thoại phiên hết hạn / bị thu hồi (alertdialog) — phủ lên trang hiện tại, không chuyển trang đột ngột,
 * để người dùng biết mình đang ở đâu. Chỉ có một lối ra là "Đăng nhập lại" (nhận focus mặc định, Esc không đóng).
 */
export function SessionExpired({ onLogin, reason = "expired" }: { onLogin: () => void; reason?: "expired" | "revoked" }) {
  const revoked = reason === "revoked";
  const loginRef = useRef<HTMLButtonElement>(null);
  return (
    <DialogPrimitive.Root open>
      <DialogPrimitive.Portal>
        <DialogOverlay />
        <DialogPrimitive.Content
          role="alertdialog"
          onOpenAutoFocus={(e) => { e.preventDefault(); loginRef.current?.focus(); }}
          onEscapeKeyDown={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
          className="fixed left-1/2 top-1/2 z-50 w-[92vw] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-strong bg-surface-3 p-6 text-foreground shadow-[var(--shadow-overlay)]"
        >
          <div className="flex items-start gap-3">
            <Lock className="mt-1 size-5 shrink-0 text-primary-text" aria-hidden />
            <div className="min-w-0">
              <DialogTitle className="pr-0">{revoked ? "Phiên đã bị thu hồi" : "Phiên đã hết hạn"}</DialogTitle>
              <DialogDescription className="mt-1 text-[0.9375rem]">
                {revoked
                  ? "Phiên đăng nhập của bạn đã bị thu hồi: bạn đã đăng xuất ở nơi khác, đổi mật khẩu, hoặc quản trị viên thu hồi. Đăng nhập lại để tiếp tục."
                  : "Vì an toàn, phiên đăng nhập có thời hạn. Đăng nhập lại để tiếp tục; dữ liệu bạn đã lưu không bị mất."}
              </DialogDescription>
            </div>
          </div>
          <Button ref={loginRef} size="lg" className="mt-5 w-full" onClick={onLogin}>
            Đăng nhập lại
          </Button>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
