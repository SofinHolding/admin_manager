import type { InventoryFilters, OpsSource, OpsUser, PlatformPolicy } from "../api";

const MAPPING_STATUS_LABEL: Record<string, string> = { mapped: "Đã map", unmapped: "Chưa map" };
const EXECUTION_MODE_LABEL: Record<string, string> = {
  primary_tool: "Primary Tool", fallback_local: "Fallback Local", disabled: "Tắt",
};
const VERIFICATION_LABEL: Record<string, string> = {
  unverified: "Chưa xác minh", valid: "Hợp lệ", invalid: "Không hợp lệ",
};
const ORIGIN_LABEL: Record<string, string> = { web_manual: "Web nhập", tool_sync: "Tool đồng bộ" };

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-2">
      <label className="mb-1 block text-[11px] font-medium text-gray-500">{label}</label>
      {children}
    </div>
  );
}

const selectCls = "h-8 w-full rounded-md border border-gray-300 bg-white px-2 text-xs outline-none focus:border-blue-500";

export function FilterBar({ filters, onChange, sources, users, platforms }: {
  filters: InventoryFilters;
  onChange: (f: InventoryFilters) => void;
  sources: OpsSource[];
  users: OpsUser[];
  platforms: PlatformPolicy[];
}) {
  const set = (patch: Partial<InventoryFilters>) => onChange({ ...filters, ...patch });

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Filter</h3>

      <Field label="User / Owner">
        <select className={selectCls} value={filters.owner_account_id || ""}
                onChange={(e) => set({ owner_account_id: e.target.value })}>
          <option value="">Tất cả user</option>
          {users.map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}
        </select>
      </Field>

      <Field label="Platform">
        <select className={selectCls} value={filters.platform || ""}
                onChange={(e) => set({ platform: e.target.value, source_instance_id: "" })}>
          <option value="">Tất cả platform</option>
          {platforms.map((p) => <option key={p.platform} value={p.platform}>{p.platform}</option>)}
        </select>
      </Field>

      <Field label="Source / Project">
        <select className={selectCls} value={filters.source_instance_id || ""}
                onChange={(e) => set({ source_instance_id: e.target.value })}>
          <option value="">Tất cả source</option>
          {sources.filter((s) => !filters.platform || s.platform === filters.platform).map((s) => (
            <option key={s.id} value={s.id}>{s.name} ({s.owner_username})</option>
          ))}
        </select>
      </Field>

      <Field label="Quốc gia">
        <input className={selectCls} value={filters.country || ""}
               onChange={(e) => set({ country: e.target.value })} placeholder="VD: Brazil" />
      </Field>

      <Field label="Origin">
        <select className={selectCls} value={filters.origin || ""}
                onChange={(e) => set({ origin: e.target.value })}>
          <option value="">Tất cả</option>
          {Object.entries(ORIGIN_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </Field>

      <Field label="Mapping status">
        <select className={selectCls} value={filters.mapping_status || ""}
                onChange={(e) => set({ mapping_status: e.target.value })}>
          <option value="">Tất cả</option>
          {Object.entries(MAPPING_STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </Field>

      <Field label="Execution mode">
        <select className={selectCls} value={filters.execution_mode || ""}
                onChange={(e) => set({ execution_mode: e.target.value })}>
          <option value="">Tất cả</option>
          {Object.entries(EXECUTION_MODE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </Field>

      <Field label="Verification status">
        <select className={selectCls} value={filters.verification_status || ""}
                onChange={(e) => set({ verification_status: e.target.value })}>
          <option value="">Tất cả</option>
          {Object.entries(VERIFICATION_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </Field>

      <Field label="Tìm kiếm">
        <input className={selectCls} value={filters.q || ""}
               onChange={(e) => set({ q: e.target.value })} placeholder="URL, username, tên…" />
      </Field>

      <button onClick={() => onChange({})}
              className="mt-1 w-full rounded-md border border-gray-300 py-1.5 text-xs hover:bg-gray-100">
        Xoá bộ lọc
      </button>
    </div>
  );
}
