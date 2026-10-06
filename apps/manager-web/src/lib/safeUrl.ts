/** Chỉ cho phép http(s) khi dựng href từ dữ liệu (chặn `javascript:`, `data:`...). Trả null nếu không an toàn. */
export function safeHttpUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    const u = new URL(raw);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}
