import { Lock } from "lucide-react";
import { Button } from "./ui/button";

/**
 * Overlay phiên hết hạn — phủ lên trang hiện tại (backdrop-blur), không redirect đột ngột.
 *
 * User thấy dashboard mờ phía sau → biết mình đang ở đâu, không bị "tự nhiên mất hết đang xem".
 */
export function SessionExpired({ onLogin }: { onLogin: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-md">
      <div className="glass mx-4 w-full max-w-sm rounded-xl p-8 text-center shadow-lg">
        <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-full bg-primary/15">
          <Lock className="size-6 text-primary-text" />
        </div>
        <h2 className="mb-2 text-lg font-semibold">Phiên đã hết hạn</h2>
        <p className="mb-6 text-sm text-muted-foreground">
          Đăng nhập lại để tiếp tục sử dụng.
        </p>
        <Button className="w-full" size="lg" onClick={onLogin}>
          Đăng nhập lại
        </Button>
      </div>
    </div>
  );
}
