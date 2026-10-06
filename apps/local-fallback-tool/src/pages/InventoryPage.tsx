/**
 * Màn hình chính Tool B — Inventory + Source Tree + Filter + Run Queue (mục 12 đặc tả
 * `tasks/backlog/local-operations-hub-v1.md`). V1 KHÔNG tải analytics metric, chỉ inventory.
 */

import { useEffect, useMemo, useState } from "react";
import {
  opsApi, type InventoryFilters, type InventoryResource, type OpsSource, type OpsUser,
  type PlatformPolicy,
} from "../api";
import { FilterBar } from "../components/FilterBar";
import { SourceTree } from "../components/SourceTree";
import { QueuePanel } from "../components/QueuePanel";

export default function InventoryPage() {
  const [resources, setResources] = useState<InventoryResource[]>([]);
  const [sources, setSources] = useState<OpsSource[]>([]);
  const [users, setUsers] = useState<OpsUser[]>([]);
  const [platforms, setPlatforms] = useState<PlatformPolicy[]>([]);
  const [filters, setFilters] = useState<InventoryFilters>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"tree" | "queue">("tree");

  const reload = () => {
    setLoading(true);
    Promise.all([
      opsApi.inventory(filters),
      opsApi.sources(),
      opsApi.users(),
      opsApi.platforms(),
    ]).then(([inv, src, usr, plt]) => {
      setResources(inv.items);
      setSources(src.items);
      setUsers(usr.items);
      setPlatforms(plt.items);
      setError("");
    }).catch((e) => setError(e instanceof Error ? e.message : "Tải inventory thất bại"))
      .finally(() => setLoading(false));
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [JSON.stringify(filters)]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const selectableResources = useMemo(
    () => resources.filter((r) => r.execution_mode === "fallback_local"),
    [resources],
  );
  const selectAllEligible = () => setSelected(new Set(selectableResources.map((r) => r.id)));
  const clearSelection = () => setSelected(new Set());

  const selectedResources = resources.filter((r) => selected.has(r.id));
  // Group theo platform để tự tách batch (mục 14 đặc tả — 1 batch chỉ 1 platform).
  const selectedByPlatform = useMemo(() => {
    const m = new Map<string, InventoryResource[]>();
    for (const r of selectedResources) {
      const list = m.get(r.platform) || [];
      list.push(r);
      m.set(r.platform, list);
    }
    return m;
  }, [selectedResources]);

  return (
    <div className="mx-auto max-w-7xl">
      <div className="mb-3 flex items-center gap-1 border-b border-gray-200">
        <button onClick={() => setTab("tree")}
                className={`px-3 py-2 text-sm font-medium ${tab === "tree" ? "border-b-2 border-blue-600 text-blue-600" : "text-gray-500"}`}>
          Inventory
        </button>
        <button onClick={() => setTab("queue")}
                className={`px-3 py-2 text-sm font-medium ${tab === "queue" ? "border-b-2 border-blue-600 text-blue-600" : "text-gray-500"}`}>
          Run Queue / Queue History
        </button>
      </div>

      {error && <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

      {tab === "tree" ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
          <FilterBar filters={filters} onChange={setFilters} sources={sources} users={users}
                     platforms={platforms} />
          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-xs text-gray-500">
                {loading ? "Đang tải…" : `${resources.length} resource · ${selected.size} đã chọn`}
              </p>
              <div className="flex gap-2">
                <button onClick={selectAllEligible}
                        className="rounded-md border border-gray-300 px-2.5 py-1 text-xs hover:bg-gray-100">
                  Chọn tất cả (đủ điều kiện queue)
                </button>
                <button onClick={clearSelection}
                        className="rounded-md border border-gray-300 px-2.5 py-1 text-xs hover:bg-gray-100">
                  Bỏ chọn
                </button>
              </div>
            </div>
            <SourceTree resources={resources} users={users} sources={sources} selected={selected} onToggle={toggleSelect} />
          </div>
        </div>
      ) : (
        <QueuePanel selectedByPlatform={selectedByPlatform} onQueued={() => { clearSelection(); reload(); }} />
      )}
    </div>
  );
}
