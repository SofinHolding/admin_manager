/**
 * Hook nạp dữ liệu analytics gắn với Filter Context.
 *
 * - Mỗi lần deps (bộ lọc, reloadKey, ...) đổi: HUỶ request đang bay (AbortController) rồi gửi request mới.
 *   Một request cũ về muộn KHÔNG bao giờ ghi đè kết quả của request mới (kiểm bằng cả `signal.aborted` lẫn số thứ tự).
 * - Dịch vụ không khả dụng (503/mạng/timeout): GIỮ dữ liệu lần trước, đặt `unavailable=true`.
 *   Lỗi khác (400/403/...): xoá dữ liệu (không hiển thị số của bộ lọc khác như thể đúng).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { isAbortError, isUnavailableError } from "../lib/errors";

export interface QueryState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
  /** Analytics không khả dụng (503/mạng/timeout) — `data` (nếu có) là dữ liệu lần tải thành công gần nhất. */
  unavailable: boolean;
  reload: () => void;
}

export function useAnalyticsQuery<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  opts: { enabled?: boolean } = {},
): QueryState<T> {
  const enabled = opts.enabled ?? true;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<Error | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [tick, setTick] = useState(0);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const seq = useRef(0);

  useEffect(() => {
    if (!enabled) { setLoading(false); return; }
    const id = ++seq.current;
    const ctl = new AbortController();
    setLoading(true);
    fetcherRef.current(ctl.signal)
      .then((d) => {
        if (ctl.signal.aborted || id !== seq.current) return;
        setData(d);
        setError(null);
        setUnavailable(false);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (isAbortError(e) || ctl.signal.aborted || id !== seq.current) return;
        const err = e instanceof Error ? e : new Error(String(e));
        setError(err);
        if (isUnavailableError(err)) {
          setUnavailable(true);               // giữ `data` cũ
        } else {
          setUnavailable(false);
          setData(null);
        }
        setLoading(false);
      });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, tick, ...deps]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, loading, error, unavailable, reload };
}
