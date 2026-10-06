/**
 * Hộp thoại hiển thị bí mật chỉ-một-lần (khóa kết nối máy `mpk_...`) vừa được tạo/xoay.
 *
 * Quy tắc bảo mật (docs/api/integration-api-v1.md §1, dev-agent prompt §6):
 *  - Chỉ hiển thị TRONG hộp thoại này; không toast, không log, không storage, không URL.
 *  - Component KHÔNG sao chép giá trị vào state riêng: nó chỉ render prop `secret`. Cha giữ giá trị
 *    trong state và PHẢI đặt về `null` khi `onClose` -> giá trị biến mất khỏi bộ nhớ React.
 *  - Không đóng khi bấm ra ngoài / Esc (tránh mất khóa do lỡ tay).
 *  - Chỉ đóng được sau khi người dùng tick "Tôi đã lưu khóa" (áp dụng cho cả nút đóng lẫn dấu X).
 */

import { useEffect, useId, useState } from "react";
import { Check, Copy, TriangleAlert } from "lucide-react";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";

export interface OneTimeSecret {
  title: string;
  /** Nhãn của trường bí mật, vd "Khóa kết nối". */
  label: string;
  value: string;
  /** Thông tin không nhạy cảm đi kèm (mã khóa, quyền, hạn dùng...). */
  details?: { label: string; value: string }[];
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* rơi xuống fallback */ }
  try {
    const el = document.createElement("textarea");
    el.value = text;
    el.setAttribute("readonly", "");
    el.style.cssText = "position:fixed;top:-9999px;left:-9999px";
    document.body.appendChild(el);
    el.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(el);
    return ok;
  } catch {
    return false;
  }
}

export function SecretOnceDialog({ secret, onClose }: { secret: OneTimeSecret | null; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const [saved, setSaved] = useState(false);
  const [needSaved, setNeedSaved] = useState(false);
  const uid = useId();
  const hintId = `${uid}-hint`;

  // Khi cha đã xóa bí mật (secret = null) thì xóa luôn trạng thái của hộp thoại.
  useEffect(() => {
    if (!secret) { setCopied(false); setCopyFailed(false); setSaved(false); setNeedSaved(false); }
  }, [secret]);

  const handleCopy = async () => {
    if (!secret) return;
    const ok = await copyText(secret.value);
    setCopied(ok);
    setCopyFailed(!ok);
  };

  const close = () => {
    setCopied(false); setCopyFailed(false); setSaved(false); setNeedSaved(false);
    onClose();
  };

  // Dấu X và mọi đường đóng khác cũng phải qua bước xác nhận đã lưu.
  const requestClose = () => {
    if (!saved) { setNeedSaved(true); return; }
    close();
  };

  return (
    <Dialog open={!!secret} onOpenChange={(open) => { if (!open) requestClose(); }}>
      <DialogContent
        className="max-w-lg"
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogTitle>{secret?.title}</DialogTitle>
        <DialogDescription className="reading mt-1 text-[0.9375rem]">
          Hãy sao chép khóa ngay — đóng hộp này là không xem lại được. Khóa chỉ hiện <strong>một lần</strong>;
          nếu mất, bạn phải tạo khóa mới.
        </DialogDescription>

        <div className="mt-4">
          <div className="mb-1 text-sm font-semibold">{secret?.label}</div>
          <code
            data-testid="secret-value"
            className="block select-all break-all rounded-md border border-border-strong bg-surface-2 px-3 py-3 font-mono text-sm"
          >
            {secret?.value}
          </code>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <Button variant="outline" onClick={handleCopy}>
              {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
              {copied ? "Đã sao chép" : "Sao chép khóa"}
            </Button>
            <span role="status" className="text-sm">
              {copied && <span className="text-success">Đã sao chép vào bộ nhớ tạm. Hãy dán vào công cụ của bạn.</span>}
              {copyFailed && <span className="text-destructive">Không sao chép được. Hãy bấm vào khóa để chọn rồi sao chép thủ công.</span>}
            </span>
          </div>
        </div>

        {secret?.details?.length ? (
          <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 border-t border-border pt-3 text-sm">
            {secret.details.map((d) => (
              <div key={d.label} className="contents">
                <dt className="text-muted-foreground">{d.label}</dt>
                <dd className="break-words">{d.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        <p className="mt-4 flex items-start gap-2 text-sm text-muted-foreground">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
          <span>Không gửi khóa qua chat hoặc email, không đưa vào mã nguồn. Ai có khóa này đều đẩy được dữ liệu thay công cụ của bạn.</span>
        </p>

        <div className="mt-5 border-t border-border pt-4">
          <label className="flex min-h-11 cursor-pointer items-start gap-3 text-[0.9375rem] font-medium">
            <input
              type="checkbox"
              checked={saved}
              onChange={(e) => { setSaved(e.target.checked); if (e.target.checked) setNeedSaved(false); }}
              aria-describedby={hintId}
              className="mt-1 size-5 shrink-0 accent-[var(--primary)]"
            />
            <span>Tôi đã lưu khóa này ở nơi an toàn</span>
          </label>
          <p id={hintId} className={needSaved ? "mt-1 text-sm font-medium text-destructive" : "mt-1 text-sm text-muted-foreground"}>
            {needSaved ? "Hãy tick ô xác nhận trước khi đóng, vì khóa sẽ không hiện lại." : "Tick ô này để đóng được hộp thoại."}
          </p>
          <div className="mt-3 flex justify-end">
            <Button onClick={requestClose} disabled={!saved}>Xong, đóng hộp này</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
