/**
 * Tùy chọn giao diện của người dùng: sáng / tối / theo máy, cỡ chữ (100 / 112 / 125 %).
 * Chỉ lưu sở thích hiển thị (không nhạy cảm) trong localStorage; mọi thao tác bọc try/catch vì có thể bị chặn.
 */

export type ThemePref = "system" | "light" | "dark";
export type TextSize = 100 | 112 | 125;

const THEME_KEY = "manager.theme";
const SIZE_KEY = "manager.textSize";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* bị chặn (chế độ riêng tư...): giữ ở bộ nhớ, bỏ qua */
  }
}

export function getThemePref(): ThemePref {
  const v = read(THEME_KEY);
  return v === "light" || v === "dark" ? v : "system";
}

export function getTextSize(): TextSize {
  const n = Number(read(SIZE_KEY));
  return n === 112 || n === 125 ? n : 100;
}

function prefersDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyTheme(pref: ThemePref): void {
  const dark = pref === "dark" || (pref === "system" && prefersDark());
  document.documentElement.classList.toggle("dark", dark);
}

/** 100 = cỡ chuẩn (gốc 15px = 93,75%); 112/125 phóng thêm 12%/25% cho người cần chữ lớn. */
export function applyTextSize(size: TextSize): void {
  document.documentElement.style.fontSize = `${(size * 0.9375).toFixed(2)}%`;
}

export function setThemePref(pref: ThemePref): void {
  write(THEME_KEY, pref);
  applyTheme(pref);
}

export function setTextSize(size: TextSize): void {
  write(SIZE_KEY, String(size));
  applyTextSize(size);
}

/** Gọi một lần trước khi render để không nháy sai giao diện; theo dõi đổi chế độ của hệ điều hành khi chọn "theo máy". */
export function initPreferences(): void {
  applyTheme(getThemePref());
  applyTextSize(getTextSize());
  if (typeof window.matchMedia === "function") {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (getThemePref() === "system") applyTheme("system");
    };
    mq.addEventListener?.("change", onChange);
  }
}
