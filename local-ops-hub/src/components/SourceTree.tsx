/**
 * Cây User -> Platform -> Source -> Resource (mục 11 đặc tả). Resource chưa gán Source nào rơi
 * vào nhóm "Chưa gán nguồn" trong platform tương ứng — KHÔNG bị ẩn đi.
 */

import { useMemo, useState } from "react";
import type { InventoryResource, OpsSource, OpsUser } from "../api";

const EXECUTION_BADGE_CLS: Record<string, string> = {
  primary_tool: "bg-blue-100 text-blue-700",
  fallback_local: "bg-green-100 text-green-700",
  disabled: "bg-gray-100 text-gray-500",
};
const EXECUTION_LABEL: Record<string, string> = {
  primary_tool: "PRIMARY TOOL", fallback_local: "FALLBACK", disabled: "DISABLED",
};
const ORIGIN_LABEL: Record<string, string> = { web_manual: "Web input", tool_sync: "Tool sync" };

interface SourceGroup { name: string; items: InventoryResource[] }
interface PlatformGroup { platform: string; sources: Map<string, SourceGroup> }
interface UserGroup { user: OpsUser | null; platforms: Map<string, PlatformGroup> }

function buildTree(resources: InventoryResource[], users: OpsUser[], sources: OpsSource[]): UserGroup[] {
  const userById = new Map(users.map((u) => [u.id, u]));
  const sourceNameById = new Map(sources.map((s) => [s.id, s.name]));
  const byUser = new Map<string, UserGroup>();
  for (const r of resources) {
    const uid = r.owner_account_id;
    if (!byUser.has(uid)) byUser.set(uid, { user: userById.get(uid) || null, platforms: new Map() });
    const ug = byUser.get(uid)!;
    if (!ug.platforms.has(r.platform)) ug.platforms.set(r.platform, { platform: r.platform, sources: new Map() });
    const pg = ug.platforms.get(r.platform)!;
    const sourceKey = r.source_ids[0] || "__none";
    if (!pg.sources.has(sourceKey)) {
      const label = sourceKey === "__none" ? "Chưa gán nguồn" : (sourceNameById.get(sourceKey) || sourceKey);
      pg.sources.set(sourceKey, { name: label, items: [] });
    }
    pg.sources.get(sourceKey)!.items.push(r);
  }
  return [...byUser.values()];
}

export function SourceTree({ resources, users, sources, selected, onToggle }: {
  resources: InventoryResource[];
  users: OpsUser[];
  sources: OpsSource[];
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  const tree = useMemo(() => buildTree(resources, users, sources), [resources, users, sources]);

  if (!resources.length) {
    return <p className="rounded-lg border border-dashed border-gray-300 p-6 text-center text-sm text-gray-400">Không có resource nào khớp bộ lọc.</p>;
  }

  return (
    <div className="space-y-3">
      {tree.map((ug) => (
        <UserNode key={ug.user?.id || "unknown"} group={ug} selected={selected} onToggle={onToggle} />
      ))}
    </div>
  );
}

function UserNode({ group, selected, onToggle }: {
  group: UserGroup; selected: Set<string>; onToggle: (id: string) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded-lg border border-gray-200 bg-white">
      <button onClick={() => setOpen((v) => !v)}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-semibold">
        <span className="text-gray-400">{open ? "▾" : "▸"}</span>
        {group.user?.username || "(không rõ owner)"}
      </button>
      {open && (
        <div className="space-y-2 border-t border-gray-100 p-2 pl-6">
          {[...group.platforms.values()].map((pg) => (
            <PlatformNode key={pg.platform} group={pg} selected={selected} onToggle={onToggle} />
          ))}
        </div>
      )}
    </div>
  );
}

function PlatformNode({ group, selected, onToggle }: {
  group: PlatformGroup; selected: Set<string>; onToggle: (id: string) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-2 text-sm font-medium text-gray-700">
        <span className="text-gray-400">{open ? "▾" : "▸"}</span>
        {group.platform}
      </button>
      {open && (
        <div className="mt-1 space-y-2 pl-5">
          {[...group.sources.values()].map((sg) => (
            <SourceNode key={sg.name} group={sg} selected={selected} onToggle={onToggle} />
          ))}
        </div>
      )}
    </div>
  );
}

function SourceNode({ group, selected, onToggle }: {
  group: SourceGroup; selected: Set<string>; onToggle: (id: string) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-2 text-xs font-medium text-gray-500">
        <span className="text-gray-400">{open ? "▾" : "▸"}</span>
        {group.name} <span className="text-gray-400">({group.items.length})</span>
      </button>
      {open && (
        <div className="mt-1 space-y-1 pl-5">
          {group.items.map((r) => <ResourceRow key={r.id} r={r} checked={selected.has(r.id)} onToggle={() => onToggle(r.id)} />)}
        </div>
      )}
    </div>
  );
}

function ResourceRow({ r, checked, onToggle }: { r: InventoryResource; checked: boolean; onToggle: () => void }) {
  const canQueue = r.execution_mode === "fallback_local";
  return (
    <div className="flex items-center gap-2 rounded-md px-2 py-1 text-xs hover:bg-gray-50">
      <input type="checkbox" checked={checked} disabled={!canQueue} onChange={onToggle}
             title={canQueue ? "" : "Chỉ resource execution_mode=fallback_local mới queue được"} />
      <span className="min-w-0 flex-1 truncate" title={r.input_ref}>{r.display_name || r.input_ref}</span>
      {r.country && <span className="shrink-0 text-gray-400">{r.country}</span>}
      <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${EXECUTION_BADGE_CLS[r.execution_mode] || "bg-gray-100 text-gray-500"}`}>
        {EXECUTION_LABEL[r.execution_mode] || r.execution_mode.toUpperCase()}
      </span>
      <span className="shrink-0 text-[10px] text-gray-400">[{ORIGIN_LABEL[r.origin] || r.origin}]</span>
    </div>
  );
}
