/**
 * useJobStream — theo dõi realtime một job qua SSE (fetch + ReadableStream, có Authorization header).
 *
 * Không dùng EventSource: EventSource không hỗ trợ header Authorization, chỉ hỗ trợ query string
 * (token sẽ lọt vào access log của nginx). Thay vào đó tự đọc ReadableStream và parse khung SSE
 * (`event: <tên>\ndata: <json>\n\n`) bằng tay.
 *
 * Nếu kết nối SSE thất bại (lỗi mạng, hạ tầng chặn streaming) → tự chuyển sang polling
 * GET /jobs/{id} + GET /jobs/{id}/items mỗi 2 giây, không cần trang gọi sửa lại.
 */

import { useEffect, useState } from "react";
import { apiFetchStream } from "../../api";
import { jobsApi, type JobCounts, type JobItem } from "../api";

export type StreamMode = "sse" | "poll" | "connecting";

/** Payload của sự kiện SSE `job` — job vừa đổi trạng thái. */
interface JobStatusEvent {
  status: string;
}

export interface JobStreamState {
  status: string | null;
  counts: JobCounts | null;
  /** Item vừa đổi trạng thái (sự kiện `item` gần nhất) — dùng để trang chi tiết biết cần refetch dòng nào. */
  lastItem: JobItem | null;
  /** Tăng lên ở mỗi lần nhận sự kiện `item`, kể cả khi nội dung giống lần trước — dùng làm dependency refetch. */
  itemTick: number;
  mode: StreamMode;
}

const POLL_INTERVAL_MS = 2000;

/** Trạng thái job đã kết thúc: không còn gì để theo dõi (không polling, không kết nối lại). */
const TERMINAL_STATUSES = new Set(["completed", "stopped", "invalid"]);

function parseSseBuffer(buffer: string, onEvent: (event: string, data: string) => void): string {
  const frames = buffer.split("\n\n");
  const remainder = frames.pop() ?? "";
  for (const frame of frames) {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length) onEvent(event, dataLines.join("\n"));
  }
  return remainder;
}

const INITIAL_STATE: JobStreamState = {
  status: null,
  counts: null,
  lastItem: null,
  itemTick: 0,
  mode: "connecting",
};

export function useJobStream(jobId: string | undefined): JobStreamState {
  const [state, setState] = useState<JobStreamState>(INITIAL_STATE);

  useEffect(() => {
    // Mọi cờ/timer sống trong CLOSURE của đúng lần chạy effect này: handler của lần chạy cũ (đổi jobId, StrictMode
    // mount-unmount-mount) không thể startPolling/ghi state cho job mới.
    setState(INITIAL_STATE); // đổi job: không giữ dữ liệu của job cũ
    if (!jobId) return;

    let cancelled = false;
    let terminal = false; // job đã completed/stopped/invalid hoặc quyền bị thu hồi ⇒ không polling
    let pollTimer: ReturnType<typeof setInterval> | undefined;
    const controller = new AbortController();
    let release: () => void = () => {};

    const update = (fn: (s: JobStreamState) => JobStreamState) => {
      if (!cancelled) setState(fn);
    };

    const stopPolling = () => {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = undefined;
      }
    };

    const startPolling = () => {
      if (cancelled || terminal || pollTimer) return;
      update((s) => ({ ...s, mode: "poll" }));
      const tick = async () => {
        try {
          const job = await jobsApi.get(jobId);
          if (cancelled) return;
          if (TERMINAL_STATUSES.has(job.status)) {
            terminal = true;
            stopPolling();
          }
          update((s) => ({
            ...s,
            status: job.status,
            counts: job.counts,
            itemTick: s.itemTick + 1,
            mode: "poll",
          }));
        } catch {
          // Bỏ qua một lần lỗi mạng, thử lại ở tick kế tiếp.
        }
      };
      void tick();
      pollTimer = setInterval(tick, POLL_INTERVAL_MS);
    };

    const startSse = async () => {
      try {
        // Qua lõi http (Bearer + refresh 401 + gắn AbortController), KHÔNG fetch thô.
        const opened = await apiFetchStream(jobsApi.streamUrl(jobId), undefined, { signal: controller.signal });
        release = opened.release;
        const r = opened.response;
        if (!r.ok || !r.body) throw new Error(`SSE lỗi ${r.status}`);

        update((s) => ({ ...s, mode: "sse" }));
        const reader = r.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          buffer = parseSseBuffer(buffer, (event, data) => {
            if (event === "ping") return;
            try {
              const parsed = JSON.parse(data);
              if (event === "counts") {
                update((s) => ({ ...s, counts: parsed as JobCounts }));
              } else if (event === "item") {
                update((s) => ({ ...s, lastItem: parsed as JobItem, itemTick: s.itemTick + 1 }));
              } else if (event === "job") {
                const st = (parsed as JobStatusEvent).status;
                if (TERMINAL_STATUSES.has(st)) terminal = true;
                update((s) => ({ ...s, status: st }));
              } else if (event === "revoked") {
                terminal = true; // quyền bị thu hồi giữa chừng: server đã đóng stream, polling chỉ ra 403
              }
            } catch {
              // Khung dữ liệu hỏng — bỏ qua, không làm chết stream.
            }
          });
        }
        // Stream đóng bình thường: job đã xong/bị thu quyền thì dừng hẳn; còn lại (đóng do nghỉ lâu...) mới polling.
        if (!cancelled && !terminal) startPolling();
      } catch {
        if (!cancelled && !terminal) startPolling();
      } finally {
        release();
      }
    };

    void startSse();

    return () => {
      cancelled = true;
      controller.abort();
      release();
      stopPolling();
    };
  }, [jobId]);

  return state;
}
