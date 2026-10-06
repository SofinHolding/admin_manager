import { useRef } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Clock } from "lucide-react";
import { Button } from "./ui/button";
import { DialogDescription, DialogOverlay, DialogTitle } from "./ui/dialog";

/** Câu đọc cho trình đọc màn hình: chỉ đổi ở vài mốc để không đọc lại mỗi giây. */
export function spokenRemaining(seconds: number): string {
  if (seconds > 60) return `khoảng ${Math.ceil(seconds / 60)} phút`;
  if (seconds > 30) return "dưới 1 phút";
  if (seconds > 10) return "dưới 30 giây";
  return "dưới 10 giây";
}

/**
 * Hộp thoại cảnh báo sắp hết phiên (alertdialog): focus bị giữ trong hộp, "Tiếp tục sử dụng" nhận focus mặc định,
 * Esc cũng là tiếp tục sử dụng (hành động an toàn); bấm ra ngoài không đóng.
 */
export function SessionWarning({
  seconds, onContinue, onLogout,
}: {
  seconds: number;
  onContinue: () => void;
  onLogout: () => void;
}) {
  const continueRef = useRef<HTMLButtonElement>(null);
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");

  return (
    <DialogPrimitive.Root open onOpenChange={(open) => { if (!open) onContinue(); }}>
      <DialogPrimitive.Portal>
        <DialogOverlay />
        <DialogPrimitive.Content
          role="alertdialog"
          onOpenAutoFocus={(e) => { e.preventDefault(); continueRef.current?.focus(); }}
          onInteractOutside={(e) => e.preventDefault()}
          className="fixed left-1/2 top-1/2 z-50 w-[92vw] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-strong bg-surface-3 p-6 text-foreground shadow-[var(--shadow-overlay)]"
        >
          <div className="flex items-start gap-3">
            <Clock className="mt-1 size-5 shrink-0 text-warning" aria-hidden />
            <div className="min-w-0">
              <DialogTitle className="pr-0">Phiên đăng nhập sắp hết hạn</DialogTitle>
              <DialogDescription className="mt-1 text-[0.9375rem]">
                Bạn đã lâu không thao tác. Vì an toàn, hệ thống sẽ tự đăng xuất bạn khi hết giờ. Bấm &ldquo;Tiếp tục sử dụng&rdquo; để giữ phiên.
              </DialogDescription>
            </div>
          </div>

          <p className="mt-4 border-y border-border py-3 text-center">
            <span className="text-sm text-muted-foreground">Còn lại </span>
            <span aria-hidden className="figure text-2xl">{mm}:{ss}</span>
            <span className="sr-only" aria-live="polite" role="status">Còn lại {spokenRemaining(seconds)}</span>
          </p>

          <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button variant="outline" size="lg" onClick={onLogout}>Đăng xuất</Button>
            <Button ref={continueRef} size="lg" onClick={onContinue}>Tiếp tục sử dụng</Button>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
