/**
 * FilterContext — MỘT nguồn sự thật cho bộ lọc Dashboard.
 *
 * - Trạng thái sống trong URL query (link chia sẻ được, back/forward hoạt động); provider đọc URL -> `filter`.
 * - Mọi KPI card và biểu đồ lấy `filter`/`params` từ `useFilterContext()` và gửi CÙNG tham số
 *   (xem filters/filterState.ts). Không component nào giữ state lọc riêng.
 * - `setFilter(patch)` gộp theo thứ tự (gọi nhiều lần trong một tick vẫn đúng) rồi ghi lại URL (replace).
 *
 * Cần nằm trong <Router>.
 */

import { createContext, useCallback, useContext, useMemo, useRef, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import {
  defaultFilter, filterToParams, filtersEqual, parseFilter, writeFilterToUrl, type FilterState,
} from "./filterState";

export interface FilterCtx {
  /** Bộ lọc đang áp dụng (đã chuẩn hoá, có ngày mặc định). */
  filter: FilterState;
  /** Tham số chuẩn gửi server (chỉ giá trị không rỗng). Danh tính ổn định khi giá trị không đổi. */
  params: Record<string, string>;
  /** Khoá chuỗi ổn định của bộ lọc — dùng làm dependency của effect. */
  key: string;
  setFilter: (patch: Partial<FilterState>) => void;
  resetFilter: () => void;
}

const Ctx = createContext<FilterCtx | null>(null);

export function useFilterContext(): FilterCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useFilterContext ngoài <FilterProvider>");
  return c;
}

export function FilterProvider({ children }: { children: ReactNode }) {
  const [sp, setSp] = useSearchParams();
  const spKey = sp.toString();

  const filter = useMemo(() => parseFilter(new URLSearchParams(spKey)), [spKey]);
  const params = useMemo(() => filterToParams(filter), [filter]);
  const key = useMemo(() => JSON.stringify(params), [params]);

  // Ref giữ giá trị mới nhất để nhiều lệnh setFilter trong cùng một tick không ghi đè nhau.
  const latest = useRef<{ filter: FilterState; sp: URLSearchParams }>({ filter, sp: new URLSearchParams(spKey) });
  latest.current = { filter, sp: new URLSearchParams(spKey) };

  const setFilter = useCallback((patch: Partial<FilterState>) => {
    const cur = latest.current;
    const merged: FilterState = { ...cur.filter, ...patch };
    if (merged.date_from > merged.date_to) {
      [merged.date_from, merged.date_to] = [merged.date_to, merged.date_from];
    }
    if (filtersEqual(merged, cur.filter)) return;
    const nextSp = writeFilterToUrl(cur.sp, merged);
    latest.current = { filter: merged, sp: nextSp };
    setSp(nextSp, { replace: true });
  }, [setSp]);

  const resetFilter = useCallback(() => {
    const next = writeFilterToUrl(new URLSearchParams(), defaultFilter());
    latest.current = { filter: defaultFilter(), sp: next };
    setSp(next, { replace: true });
  }, [setSp]);

  const value = useMemo<FilterCtx>(
    () => ({ filter, params, key, setFilter, resetFilter }),
    [filter, params, key, setFilter, resetFilter],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
