import { Button } from "./ui/button";

/** Dialog cảnh báo sắp hết phiên — đếm ngược realtime. */
export function SessionWarning({
  seconds, onContinue, onLogout,
}: {
  seconds: number;
  onContinue: () => void;
  onLogout: () => void;
}) {
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="glass mx-4 w-full max-w-md rounded-xl p-6 text-center shadow-lg">
        <div className="mb-2 text-2xl">⚠️</div>
        <h2 className="mb-2 text-lg font-semibold">Phiên sắp hết hạn</h2>
        <p className="mb-4 text-sm text-muted-foreground">
          Bạn sẽ tự động đăng xuất sau{" "}
          <span className="font-mono font-semibold text-warning">{mm}:{ss}</span>{" "}
          do không hoạt động.
        </p>
        <div className="flex justify-center gap-3">
          <Button onClick={onContinue}>Tiếp tục sử dụng</Button>
          <Button variant="outline" onClick={onLogout}>Đăng xuất</Button>
        </div>
      </div>
    </div>
  );
}
