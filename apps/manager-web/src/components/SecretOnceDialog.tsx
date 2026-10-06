/**
 * Modal hiển thị bí mật chỉ-một-lần (API key máy `mpk_...`) vừa được cấp/xoay.
 *
 * Quy tắc bảo mật (docs/api/integration-api-v1.md §1, dev-agent prompt §6):
 *  - Chỉ hiển thị TRONG modal này; không toast, không log, không storage, không URL.
 *  - Component KHÔNG sao chép giá trị vào state riêng: nó chỉ render prop `secret`. Cha giữ giá trị
 *    trong state và PHẢI đặt về `null` khi `onClose` -> giá trị biến mất khỏi bộ nhớ React.
 *  - Không đóng khi bấm ra ngoài / Esc (tránh mất key do lỡ tay): chỉ nút "Đã lưu, đóng" hoặc dấu X.
 */

import { useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, Check, Copy } from "lucide-react";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";

export interface OneTimeSecret {
  title: string;
  /** Nhãn của trường bí mật, vd "API key". */
  label: string;
  value: string;
  /** Thông tin không nhạy cảm đi kèm (key_id, scopes...). */
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

  const handleCopy = async () => {
    if (!secret) return;
    const ok = await copyText(secret.value);
    setCopied(ok);
    if (ok) toast.success("Đã copy vào clipboard"); else toast.error("Không copy được — hãy chọn và copy thủ công");
  };

  const close = () => { setCopied(false); onClose(); };

  return (
    <Dialog open={!!secret} onOpenChange={(open) => { if (!open) close(); }}>
      <DialogContent
        className="max-w-lg"
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogTitle>{secret?.title}</DialogTitle>
        <DialogDescription>
          Chỉ hiển thị <strong>một lần</strong>. Hãy lưu vào nơi an toàn (trình quản lý bí mật) ngay bây giờ —
          đóng cửa sổ này là không thể xem lại.
        </DialogDescription>

        <div className="mt-3 flex items-start gap-2 rounded-md border border-yellow-500/30 bg-yellow-500/10 px-3 py-2 text-xs text-yellow-700">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>Không chia sẻ, không dán vào chat/ticket, không commit vào mã nguồn.</span>
        </div>

        {secret?.details?.length ? (
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            {secret.details.map((d) => (
              <div key={d.label} className="contents">
                <dt className="text-muted-foreground">{d.label}</dt>
                <dd className="font-mono break-all">{d.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        <div className="mt-3">
          <div className="mb-1 text-xs text-muted-foreground">{secret?.label}</div>
          <code
            data-testid="secret-value"
            className="block select-all break-all rounded-md border border-border bg-elev-1 px-3 py-2 font-mono text-xs"
          >
            {secret?.value}
          </code>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={handleCopy}>
            {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
            {copied ? "Đã copy" : "Copy"}
          </Button>
          <Button onClick={close}>Đã lưu, đóng</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
