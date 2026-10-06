/**
 * "Khóa dữ liệu" — giữ nguyên SỐ LIỆU ĐANG HIỂN THỊ trên trang thống kê tối đa 1 giờ.
 *
 * CHỈ là khóa phía giao diện: dữ liệu vẫn được Tool đẩy lên và Analytics vẫn đồng bộ vào cơ sở dữ liệu như bình thường;
 * khóa chỉ làm cho các lần tải trong lúc khóa dùng lại kết quả đã có thay vì hỏi server lại.
 *
 * Cách hoạt động:
 *  - Mỗi truy vấn dữ liệu có `cacheKey` (tên + bộ lọc). Mọi kết quả thành công được nhớ trong bộ nhớ (kể cả TRƯỚC khi khóa).
 *  - Đang khóa: truy vấn có kết quả nhớ → dùng ngay, không gọi server. Chưa từng xem tổ hợp lọc đó → lấy số liệu hiện tại và nhớ lại
 *    (không thể "quay ngược" về số cũ cho thứ chưa từng xem — Analytics chỉ giữ trạng thái mới nhất).
 *  - Hết 1 giờ hoặc người dùng mở khóa: xóa bộ nhớ, tăng `epoch` để mọi truy vấn tải lại số liệu mới nhất.
 *  - Không lưu xuống đĩa (không localStorage/sessionStorage): tải lại trang (F5) là mở khóa.
 */

import { useEffect, useSyncExternalStore } from "react";

export const LOCK_MS = 60 * 60 * 1000;
const MAX_ENTRIES = 300;

let until = 0;
let epoch = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
const cache = new Map<string, unknown>();
const subs = new Set<() => void>();
let version = 0; // thay đổi khi trạng thái khóa đổi (cho useSyncExternalStore)

function emit(): void {
  version += 1;
  subs.forEach((fn) => fn());
}

function schedule(): void {
  if (timer) clearTimeout(timer);
  if (!until) return;
  const wait = Math.max(0, Math.min(until - Date.now(), 2 ** 31 - 1));
  timer = setTimeout(() => {
    if (until && Date.now() >= until) unlock();
    else schedule();
  }, wait);
}

export function isLocked(now: number = Date.now()): boolean {
  return until > now;
}

export function lockUntil(): number {
  return until;
}

export function lockData(now: number = Date.now()): void {
  until = now + LOCK_MS;
  schedule();
  emit();
}

export function unlockData(): void {
  unlock();
}

function unlock(): void {
  if (timer) clearTimeout(timer);
  timer = undefined;
  until = 0;
  cache.clear();
  epoch += 1;
  emit();
}

export function cacheGet<T>(key: string): T | undefined {
  return cache.get(key) as T | undefined;
}

export function cacheSet(key: string, value: unknown): void {
  if (cache.size >= MAX_ENTRIES) cache.delete(cache.keys().next().value as string); // bỏ mục cũ nhất
  cache.set(key, value);
}

/** Dùng trong test: về trạng thái ban đầu. */
export function resetDataLockForTests(): void {
  if (timer) clearTimeout(timer);
  timer = undefined;
  until = 0;
  epoch = 0;
  version = 0;
  cache.clear();
  emit();
}

/** Gắn bộ nhớ khóa với người dùng đang đăng nhập: đổi người ⇒ mở khóa và xóa bộ nhớ (không để số liệu của người này lộ sang người khác). */
let boundUser: string | undefined;
export function bindLockToUser(id: string | undefined): void {
  if (boundUser !== undefined && id !== boundUser && (until || cache.size)) unlock();
  boundUser = id;
}

export function getEpoch(): number {
  return epoch;
}

function subscribe(fn: () => void): () => void {
  subs.add(fn);
  return () => subs.delete(fn);
}

export interface DataLockState {
  locked: boolean;
  /** Mốc hết khóa (ms epoch), 0 nếu không khóa. */
  until: number;
  /** Tăng mỗi khi mở khóa/hết hạn: đưa vào deps để truy vấn tải lại. */
  epoch: number;
  /** Số phút còn lại (làm tròn lên), 0 nếu không khóa. */
  minutesLeft: number;
  lock: () => void;
  unlock: () => void;
}

export function useDataLock(): DataLockState {
  useSyncExternalStore(subscribe, () => version, () => version);
  const locked = isLocked();
  // Cập nhật số phút còn lại mỗi 30 giây khi đang khóa
  useEffect(() => {
    if (!locked) return;
    const id = setInterval(emit, 30_000);
    return () => clearInterval(id);
  }, [locked]);
  return {
    locked,
    until,
    epoch,
    minutesLeft: locked ? Math.max(1, Math.ceil((until - Date.now()) / 60_000)) : 0,
    lock: lockData,
    unlock: unlockData,
  };
}
