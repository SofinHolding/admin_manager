/** Tiện ích test cho các trang Phân phối điểm: bọc Router + AuthContext, dữ liệu mẫu. Chỉ dùng trong test. */
import { vi } from "vitest";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthContext, type AuthCtx, type User } from "../../auth";
import type { JobCounts, JobSummary } from "../api";

export function authValue(user: User): AuthCtx {
  return {
    user, loading: false, mode: "password", sessionExpired: false, sessionReason: "expired", showWarning: false,
    warningSeconds: 300, login: vi.fn(), loginWithOidc: vi.fn(), completeOidcLogin: vi.fn(), logout: vi.fn(),
    continueSession: vi.fn(), dismissExpired: vi.fn(), hasPerm: () => true,
  };
}

export const discordUser: User = { id: "u1", username: "vanh", email: "v@x", role: "discord", perms: ["reward:access"] };
export const adminUser: User = { id: "a1", username: "root", email: "r@x", role: "admin", perms: [] };

export function renderAt(ui: ReactElement, path: string, routePath: string, user: User = discordUser) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthContext.Provider value={authValue(user)}>
        <Routes>
          <Route path={routePath} element={ui} />
          <Route path="/reward/jobs/:id" element={<p>TRANG CHI TIẾT</p>} />
          <Route path="*" element={<p>TRANG KHÁC</p>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

export const counts = (o: Partial<JobCounts> = {}): JobCounts => ({
  total: 300, pending: 170, processing: 0, retrying: 0, success: 120, failed: 7, unknown: 3, skipped: 0, total_points: 45000, ...o,
});

export const jobSummary = (o: Partial<JobSummary> = {}): JobSummary => ({
  id: "1", name: "Tuần 38 - Top voice", status: "running", total_items: 300, counts: counts(),
  created_at: "2026-10-01T00:00:00Z", started_at: new Date(Date.now() - 5 * 60_000).toISOString(), finished_at: null, ...o,
});

/** Stream SSE điều khiển tay. */
export function sseStream() {
  const enc = new TextEncoder();
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(c) { ctl = c; } });
  return {
    response: () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (event: string, data: unknown) => ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    close: () => ctl.close(),
  };
}
