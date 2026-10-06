/** Tiện ích test: fetch giả ở RANH GIỚI MẠNG (không mock code nghiệp vụ). */
import { vi } from "vitest";

export interface FetchCall {
  url: string;
  path: string;
  params: URLSearchParams;
  method: string;
  headers: Headers;
  body: string | null;
  signal: AbortSignal | null | undefined;
}

export type Responder = (call: FetchCall) => Response | Promise<Response> | undefined | null | void;

export function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200, headers: { "Content-Type": "application/json" }, ...init,
  });
}

export function parseCall(input: RequestInfo | URL, init?: RequestInit): FetchCall {
  const raw = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  const u = new URL(raw, "http://localhost");
  return {
    url: raw, path: u.pathname, params: u.searchParams,
    method: (init?.method ?? "GET").toUpperCase(),
    headers: new Headers(init?.headers),
    body: typeof init?.body === "string" ? init.body : null,
    signal: init?.signal,
  };
}

/**
 * Cài `fetch` giả. `responders` được thử lần lượt; trả về undefined thì thử cái kế tiếp.
 * Tôn trọng AbortSignal: huỷ -> reject AbortError (giống trình duyệt).
 */
export function installFetch(...responders: Responder[]) {
  const calls: FetchCall[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = parseCall(input, init);
    calls.push(call);
    const sig = init?.signal;
    if (sig?.aborted) throw new DOMException("aborted", "AbortError");
    const abortP = sig
      ? new Promise<never>((_, rej) => sig.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")), { once: true }))
      : null;
    abortP?.catch(() => {}); // tránh unhandled rejection khi request đã xong rồi mới bị huỷ
    for (const r of responders) {
      const res = r(call);
      if (res) return abortP ? Promise.race([Promise.resolve(res), abortP]) : res;
    }
    return jsonResponse({ detail: "not mocked: " + call.path }, { status: 404 });
  });
  vi.stubGlobal("fetch", fn);
  return { calls, fn, byPath: (p: string | RegExp) => calls.filter((c) => typeof p === "string" ? c.path === p : p.test(c.path)) };
}

/** Promise có thể giải phóng từ bên ngoài — để kiểm tra thứ tự phản hồi (cũ về muộn). */
export function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

export function fakeJwt(claims: Record<string, unknown>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "RS256", kid: "k1", typ: "at+jwt" })}.${b64(claims)}.${Buffer.from("sig-not-checked-in-ui").toString("base64url")}`;
}
