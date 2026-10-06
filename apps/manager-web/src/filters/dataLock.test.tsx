import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import {
  LOCK_MS, bindLockToUser, cacheGet, cacheSet, isLocked, lockData, resetDataLockForTests, unlockData, useDataLock,
} from "./dataLock";
import { useAnalyticsQuery } from "./useAnalyticsQuery";

afterEach(() => { vi.useRealTimers(); resetDataLockForTests(); });

describe("dataLock — trạng thái và hết hạn", () => {
  it("khóa kéo dài đúng 1 giờ rồi tự mở, xóa bộ nhớ và tăng epoch", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useDataLock());
    const e0 = result.current.epoch;
    cacheSet("k", { a: 1 });
    act(() => lockData());
    expect(result.current.locked).toBe(true);
    expect(result.current.minutesLeft).toBe(60);
    act(() => { vi.advanceTimersByTime(LOCK_MS - 1000); });
    expect(isLocked()).toBe(true);
    expect(cacheGet("k")).toEqual({ a: 1 });
    act(() => { vi.advanceTimersByTime(2000); });
    expect(isLocked()).toBe(false);
    expect(result.current.locked).toBe(false);
    expect(cacheGet("k")).toBeUndefined();
    expect(result.current.epoch).toBe(e0 + 1);
  });

  it("mở khóa tay xóa bộ nhớ; đổi người dùng đang đăng nhập cũng mở khóa (không rò số liệu giữa người)", () => {
    bindLockToUser("u1");
    lockData();
    cacheSet("k", 1);
    bindLockToUser("u1");
    expect(isLocked()).toBe(true);
    bindLockToUser("u2");
    expect(isLocked()).toBe(false);
    expect(cacheGet("k")).toBeUndefined();
    lockData();
    unlockData();
    expect(isLocked()).toBe(false);
  });
});

describe("useAnalyticsQuery + khóa dữ liệu", () => {
  beforeEach(() => resetDataLockForTests());

  it("đang khóa: tổ hợp đã xem dùng bộ nhớ (không gọi server); tổ hợp mới gọi và được nhớ; mở khóa => tải lại số mới", async () => {
    const calls: string[] = [];
    let n = 0;
    const fetcherFor = (k: string) => async () => { calls.push(k); n += 1; return { k, n }; };
    const { result, rerender } = renderHook(
      ({ k }: { k: string }) => useAnalyticsQuery(fetcherFor(k), [k], { cacheKey: `q|${k}` }),
      { initialProps: { k: "A" } },
    );
    await waitFor(() => expect(result.current.data).toEqual({ k: "A", n: 1 }));

    act(() => lockData());
    rerender({ k: "B" });                                       // chưa xem B => lấy hiện tại
    await waitFor(() => expect(result.current.data).toEqual({ k: "B", n: 2 }));
    rerender({ k: "A" });                                       // A đã xem => dùng bộ nhớ, KHÔNG gọi server
    await waitFor(() => expect(result.current.data).toEqual({ k: "A", n: 1 }));
    expect(calls).toEqual(["A", "B"]);

    act(() => unlockData());                                    // mở khóa => tải lại A lấy số mới nhất
    await waitFor(() => expect(result.current.data).toEqual({ k: "A", n: 3 }));
    expect(calls).toEqual(["A", "B", "A"]);
  });

  it("không có cacheKey => luôn gọi server dù đang khóa (danh mục, không phải số liệu)", async () => {
    let n = 0;
    const { result, rerender } = renderHook(
      ({ k }: { k: string }) => useAnalyticsQuery(async () => ({ n: ++n }), [k]),
      { initialProps: { k: "A" } },
    );
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));
    act(() => lockData());
    rerender({ k: "B" });
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }));
  });
});
