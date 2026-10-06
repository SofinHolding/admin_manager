import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useJobStream } from "./useJobStream";
import { __resetHttpForTests } from "../../lib/http";
import { clearTokens, getAccessToken, setTokens } from "../../auth/tokenStore";
import { installFetch, jsonResponse } from "../../test/helpers";

/** Stream SSE điều khiển tay: `push` đẩy khung, `close` kết thúc bình thường, `fail` làm stream lỗi. */
function sseStream() {
  const enc = new TextEncoder();
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(c) { ctl = c; } });
  return {
    response: () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (event: string, data: unknown) => ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    close: () => ctl.close(),
    fail: () => ctl.error(new Error("mạng đứt")),
  };
}

const jobBody = (id: string, status: string) => ({ id, status, counts: { pending: 1 } });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

beforeEach(() => { __resetHttpForTests(); clearTokens(); setTokens("AT", "RT", false); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("useJobStream", () => {
  it("đổi jobId: stream cũ lỗi muộn KHÔNG polling job cũ và không ghi dữ liệu cũ vào state job mới", async () => {
    const s1 = sseStream();
    const s2 = sseStream();
    const f = installFetch(
      (c) => c.path === "/v1/reward/jobs/1/stream" ? s1.response() : undefined,
      (c) => c.path === "/v1/reward/jobs/2/stream" ? s2.response() : undefined,
      (c) => /^\/v1\/reward\/jobs\/\d+$/.test(c.path) ? jsonResponse(jobBody(c.path.split("/").pop()!, "running")) : undefined,
    );
    const { result, rerender } = renderHook(({ id }) => useJobStream(id), { initialProps: { id: "1" } });
    await waitFor(() => expect(result.current.mode).toBe("sse"));
    act(() => s1.push("job", { status: "running" }));
    await waitFor(() => expect(result.current.status).toBe("running"));

    rerender({ id: "2" });
    await waitFor(() => expect(f.byPath("/v1/reward/jobs/2/stream")).toHaveLength(1));
    expect(result.current.status).toBeNull(); // không giữ dữ liệu job 1

    s1.fail(); // handler của lần chạy cũ chạy muộn
    await sleep(100);
    expect(f.byPath("/v1/reward/jobs/1")).toHaveLength(0); // không polling job cũ
    act(() => s2.push("job", { status: "paused" }));
    await waitFor(() => expect(result.current.status).toBe("paused"));
    expect(result.current.mode).toBe("sse");
  });

  it("job completed rồi stream đóng bình thường -> KHÔNG polling", async () => {
    const s = sseStream();
    const f = installFetch(
      (c) => c.path === "/v1/reward/jobs/7/stream" ? s.response() : undefined,
      (c) => c.path === "/v1/reward/jobs/7" ? jsonResponse(jobBody("7", "completed")) : undefined,
    );
    const { result } = renderHook(() => useJobStream("7"));
    await waitFor(() => expect(result.current.mode).toBe("sse"));
    act(() => { s.push("job", { status: "completed" }); s.close(); });
    await waitFor(() => expect(result.current.status).toBe("completed"));
    await sleep(150);
    expect(f.byPath("/v1/reward/jobs/7")).toHaveLength(0);
    expect(result.current.mode).toBe("sse");
  });

  it("stream đóng khi job chưa xong (vd. nghỉ lâu) -> rơi về polling", async () => {
    const s = sseStream();
    const f = installFetch(
      (c) => c.path === "/v1/reward/jobs/8/stream" ? s.response() : undefined,
      (c) => c.path === "/v1/reward/jobs/8" ? jsonResponse(jobBody("8", "paused")) : undefined,
    );
    const { result } = renderHook(() => useJobStream("8"));
    await waitFor(() => expect(result.current.mode).toBe("sse"));
    act(() => { s.push("job", { status: "paused" }); s.close(); });
    await waitFor(() => expect(result.current.mode).toBe("poll"));
    expect(f.byPath("/v1/reward/jobs/8").length).toBeGreaterThanOrEqual(1);
  });

  it("SSE nhận 401 -> refresh token một lần rồi mở lại stream với token mới (đi qua lõi http)", async () => {
    const s = sseStream();
    const f = installFetch(
      (c) => c.path === "/v1/auth/refresh" ? jsonResponse({ access_token: "AT-new", refresh_token: "RT-new" }) : undefined,
      (c) => {
        if (c.path !== "/v1/reward/jobs/9/stream") return undefined;
        return c.headers.get("Authorization") === "Bearer AT" ? jsonResponse({ detail: "hết hạn" }, { status: 401 }) : s.response();
      },
    );
    const { result } = renderHook(() => useJobStream("9"));
    await waitFor(() => expect(result.current.mode).toBe("sse"));
    expect(f.byPath("/v1/auth/refresh")).toHaveLength(1);
    const streams = f.byPath("/v1/reward/jobs/9/stream");
    expect(streams).toHaveLength(2);
    expect(streams[1].headers.get("Authorization")).toBe("Bearer AT-new");
    expect(getAccessToken()).toBe("AT-new");
  });

  it("unmount huỷ kết nối (signal bị abort)", async () => {
    const s = sseStream();
    const f = installFetch((c) => c.path === "/v1/reward/jobs/3/stream" ? s.response() : undefined);
    const { result, unmount } = renderHook(() => useJobStream("3"));
    await waitFor(() => expect(result.current.mode).toBe("sse"));
    unmount();
    expect(f.byPath("/v1/reward/jobs/3/stream")[0].signal?.aborted).toBe(true);
  });
});
