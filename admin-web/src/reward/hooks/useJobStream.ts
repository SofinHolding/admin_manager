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

import { useEffect, useRef, useState } from "react";
import { getAccessToken, jobsApi, type JobCounts, type JobItem } from "../api";

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

export function useJobStream(jobId: string | undefined): JobStreamState {
  const [state, setState] = useState<JobStreamState>({
    status: null,
    counts: null,
    lastItem: null,
    itemTick: 0,
    mode: "connecting",
  });
  const abortRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<number | undefined>(undefined);
  const stoppedRef = useRef(false);

  useEffect(() => {
    stoppedRef.current = false;
    if (!jobId) return;

    const startPolling = () => {
      if (pollTimerRef.current) return;
      setState((s) => ({ ...s, mode: "poll" }));
      const tick = async () => {
        try {
          const job = await jobsApi.get(jobId);
          if (stoppedRef.current) return;
          setState((s) => ({
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
      tick();
      pollTimerRef.current = setInterval(tick, POLL_INTERVAL_MS);
    };

    const startSse = async () => {
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const token = getAccessToken();
        const r = await fetch(jobsApi.streamUrl(jobId), {
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
          signal: controller.signal,
        });
        if (!r.ok || !r.body) throw new Error(`SSE lỗi ${r.status}`);

        setState((s) => ({ ...s, mode: "sse" }));
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
                setState((s) => ({ ...s, counts: parsed as JobCounts }));
              } else if (event === "item") {
                setState((s) => ({ ...s, lastItem: parsed as JobItem, itemTick: s.itemTick + 1 }));
              } else if (event === "job") {
                setState((s) => ({ ...s, status: (parsed as JobStatusEvent).status }));
              }
            } catch {
              // Khung dữ liệu hỏng — bỏ qua, không làm chết stream.
            }
          });
        }
        if (!stoppedRef.current) startPolling();
      } catch {
        if (!stoppedRef.current) startPolling();
      }
    };

    startSse();

    return () => {
      stoppedRef.current = true;
      abortRef.current?.abort();
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = undefined;
      }
    };
  }, [jobId]);

  return state;
}
