/**
 * Run Queue (tạo batch từ resource đã chọn — tự tách theo platform, mục 14 đặc tả) + Queue
 * History (mục 12). V1 không có adapter nên mọi item vừa thêm dừng ở `blocked_no_adapter` —
 * KHÔNG hiển thị như đã chạy xong.
 */

import { useEffect, useState } from "react";
import { opsApi, type InventoryResource } from "../api";

const STATUS_CLS: Record<string, string> = {
  queued: "bg-blue-100 text-blue-700",
  blocked_no_adapter: "bg-amber-100 text-amber-700",
  ready: "bg-green-100 text-green-700",
  running: "bg-blue-100 text-blue-700",
  success: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-gray-100 text-gray-500",
};

interface BatchSummary { id: string; platform: string; name: string; status: string; created_at: string }
interface BatchItem { id: string; resource_id: string; status: string; owner_account_id: string }

export function QueuePanel({ selectedByPlatform, onQueued }: {
  selectedByPlatform: Map<string, InventoryResource[]>;
  onQueued: () => void;
}) {
  const [batches, setBatches] = useState<BatchSummary[]>([]);
  const [expanded, setExpanded] = useState<string>("");
  const [items, setItems] = useState<BatchItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);

  const loadBatches = () => opsApi.listBatches().then((d) => setBatches(d.items)).catch(() => {});
  useEffect(() => { loadBatches(); }, []);

  const expand = async (batchId: string) => {
    if (expanded === batchId) { setExpanded(""); return; }
    setExpanded(batchId);
    const d = await opsApi.getBatch(batchId);
    setItems(d.items);
  };

  const cancelItem = async (itemId: string) => {
    await opsApi.cancelItem(itemId).catch(() => {});
    if (expanded) {
      const d = await opsApi.getBatch(expanded);
      setItems(d.items);
    }
  };

  const createAll = async () => {
    setBusy(true);
    const lines: string[] = [];
    for (const [platform, group] of selectedByPlatform) {
      try {
        const batch = await opsApi.createBatch(platform, `${platform} — ${new Date().toLocaleString("vi-VN")}`);
        const res = await opsApi.addItems(batch.id, group.map((r) => r.id));
        lines.push(`${platform}: tạo batch "${batch.name}" — thêm ${res.added.length}, bỏ qua ${res.skipped.length}`);
      } catch (e) {
        lines.push(`${platform}: lỗi — ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    setLog(lines);
    setBusy(false);
    loadBatches();
    onQueued();
  };

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <h3 className="mb-2 text-sm font-semibold">Tạo Run Queue từ resource đã chọn</h3>
        {selectedByPlatform.size === 0 ? (
          <p className="text-xs text-gray-400">Chưa chọn resource nào ở tab Inventory.</p>
        ) : (
          <div className="space-y-1.5">
            {[...selectedByPlatform.entries()].map(([platform, group]) => (
              <div key={platform} className="flex items-center justify-between rounded-md border border-gray-100 px-2.5 py-1.5 text-xs">
                <span className="font-medium">{platform}</span>
                <span className="text-gray-500">{group.length} resource → 1 batch riêng</span>
              </div>
            ))}
            <button onClick={createAll} disabled={busy}
                    className="mt-2 w-full rounded-md bg-blue-600 py-2 text-xs font-medium text-white disabled:opacity-50">
              {busy ? "Đang tạo…" : `Tạo ${selectedByPlatform.size} batch`}
            </button>
          </div>
        )}
        {log.length > 0 && (
          <div className="mt-3 space-y-1 border-t border-gray-100 pt-2 text-[11px] text-gray-600">
            {log.map((l, i) => <p key={i}>{l}</p>)}
          </div>
        )}
      </div>

      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <h3 className="mb-2 text-sm font-semibold">Queue History</h3>
        {batches.length === 0 ? (
          <p className="text-xs text-gray-400">Chưa có batch nào.</p>
        ) : (
          <div className="space-y-1">
            {batches.map((b) => (
              <div key={b.id} className="rounded-md border border-gray-100">
                <button onClick={() => expand(b.id)} className="flex w-full items-center justify-between px-2.5 py-1.5 text-left text-xs">
                  <span>{b.name || b.id.slice(0, 8)} <span className="text-gray-400">· {b.platform}</span></span>
                  <span className="text-gray-400">{b.status}</span>
                </button>
                {expanded === b.id && (
                  <div className="space-y-1 border-t border-gray-100 p-2">
                    {items.map((it) => (
                      <div key={it.id} className="flex items-center justify-between text-[11px]">
                        <span className={`rounded px-1.5 py-0.5 font-medium ${STATUS_CLS[it.status] || "bg-gray-100 text-gray-500"}`}>
                          {it.status}
                        </span>
                        {it.status !== "cancelled" && it.status !== "success" && it.status !== "failed" && (
                          <button onClick={() => cancelItem(it.id)} className="text-red-500 hover:underline">Huỷ</button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
