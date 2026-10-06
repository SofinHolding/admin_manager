import "@testing-library/jest-dom/vitest";
import { webcrypto } from "node:crypto";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// jsdom không có crypto.subtle -> dùng WebCrypto thật của Node (PKCE cần SHA-256 thật, không giả).
if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
}

// jsdom thiếu một số API trình duyệt mà recharts/radix dùng
class RO { observe() {} unobserve() {} disconnect() {} }
class IO {
  constructor(private cb: IntersectionObserverCallback) {}
  observe(el: Element) { this.cb([{ isIntersecting: true, target: el } as IntersectionObserverEntry], this as unknown as IntersectionObserver); }
  unobserve() {} disconnect() {} takeRecords() { return []; }
}
// Gán trực tiếp (không dùng vi.stubGlobal) để `vi.unstubAllGlobals()` trong từng test không gỡ mất các polyfill này.
Object.assign(globalThis, { ResizeObserver: RO, IntersectionObserver: IO });
if (!window.matchMedia) {
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView || (() => {});
Element.prototype.hasPointerCapture = Element.prototype.hasPointerCapture || (() => false);
Element.prototype.setPointerCapture = Element.prototype.setPointerCapture || (() => {});
Element.prototype.releasePointerCapture = Element.prototype.releasePointerCapture || (() => {});

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
});
