#!/usr/bin/env python3
"""
tiktok_links.py — lấy TẤT CẢ link video của một kênh TikTok về máy (lưu DB cục bộ + file .txt).

TikTok chặn việc liệt kê video kênh khi thiếu cookie phiên hợp lệ; có cookie + ĐÚNG User-Agent + ĐÚNG proxy thì yt-dlp (`TikTokUserIE`) liệt kê ổn
định, mới nhất trước (cơ chế đã đo thật ở youtube_video). Cookie lấy từ Chrome/Edge cục bộ chạy ẩn (`browser_cookie.py`), KHÔNG cần AdsPower;
proxy lấy từ danh sách proxy đã cài (không có proxy => đi thẳng bằng IP máy này).

Xoay vòng: thử lần lượt từng proxy; mỗi proxy dùng lại phiên (cookie+UA) đã lưu nếu còn, lỗi "đáng làm mới" thì mở trình duyệt lấy phiên mới đúng MỘT
lần (phiên vừa lấy < 10 phút mà vẫn lỗi => lỗi nằm ở IP/proxy, đổi proxy chứ không mở lại trình duyệt).

CLI (Electron gọi, dữ liệu qua stdin):
  echo '{"channel_id":"<id kênh trong DB>","url":"https://www.tiktok.com/@abc","limit":1500}' | python tiktok_links.py fetch
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import browser_cookie
import channels_db
from pools import force_remote_dns, load_pools, mask_proxy

SESSION_FILE = Path(channels_db.ROOT_DIR) / ".dev" / "tiktok-session.json"
SETTINGS_FILE = Path(channels_db.ROOT_DIR) / ".dev" / "collector-config.json"
EXPORT_DIR = Path(channels_db.ROOT_DIR) / ".dev" / "video-links"
FRESH_SESSION_S = 600.0
FULL_MAX_ITEMS = 1500            # TikTok phân trang kiểu cursor: xin đủ MỘT lần, không escalate (escalate fetch dư và dễ bị chặn)
_USER_RE = re.compile(r"tiktok\.com/@([\w.\-]+)", re.I)
_HANDLE_RE = re.compile(r"^@?([\w.\-]{2,64})$")


class TikTokError(Exception):
    """Lỗi thân thiện, hiển thị thẳng cho người dùng."""


class EmptyChannel(Exception):
    """Kênh thật sự chưa có video (không phải lỗi)."""


class PrivateChannel(Exception):
    """Kênh riêng tư / không tồn tại — làm mới cookie không giải quyết được."""


class RefreshableError(Exception):
    """Cookie hết hạn / bị chặn / không trích được phiên — đáng làm mới cookie hoặc đổi proxy."""


def classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "does not have any videos posted" in msg:
        return "empty"
    if "account is private" in msg or "log into an account" in msg:
        return "private"
    return "refreshable"


def username_of(ref: str) -> str:
    m = _USER_RE.search(ref or "") or _HANDLE_RE.match((ref or "").strip())
    return m.group(1) if m else ""


def channel_url(ref: str) -> str:
    user = username_of(ref)
    if not user:
        raise TikTokError("Không đọc được tên kênh TikTok (cần link dạng https://www.tiktok.com/@tenkenh hoặc @tenkenh)")
    return f"https://www.tiktok.com/@{user}"


def _iso(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def entry_to_video(entry: dict, username: str) -> dict | None:
    vid = str(entry.get("id") or "").strip()
    if not vid:
        return None
    views = entry.get("view_count")
    return {"video_id": vid, "url": entry.get("webpage_url") or entry.get("url") or f"https://www.tiktok.com/@{username}/video/{vid}",
            "title": str(entry.get("title") or entry.get("description") or "")[:300], "published_at": _iso(entry.get("timestamp")),
            "views": views if isinstance(views, int) and not isinstance(views, bool) else None}


def write_cookiejar(cookies: list[dict]) -> str:
    """Netscape cookie file tạm cho yt-dlp — người gọi PHẢI xoá sau khi dùng."""
    fd, path = tempfile.mkstemp(prefix="tiktok_cookies_", suffix=".txt")
    expires = int(time.time()) + 30 * 86400
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        name = c.get("name") or ""
        if not name:
            continue
        domain = c.get("domain") or ".tiktok.com"
        domain = domain if domain.startswith(".") else "." + domain
        lines.append("\t".join([domain, "TRUE", c.get("path") or "/", "FALSE", str(expires), name, c.get("value") or ""]))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


Extract = Callable[[str, str, str | None, str | None, int], list[dict]]


def _extract_flat(url: str, cookiefile: str, user_agent: str | None, proxy: str | None, limit: int) -> list[dict]:
    import yt_dlp

    opts: dict = {"extract_flat": True, "quiet": True, "no_warnings": True, "skip_download": True, "cookiefile": cookiefile,
                  "http_headers": {"User-Agent": user_agent} if user_agent else {}, "proxy": force_remote_dns(proxy) or None,
                  "socket_timeout": 20.0, "playlistend": limit}
    try:   # ép TLS fingerprint chrome-136: target mới nhất đang bị TikTok chặn không ổn định (đo thật ở youtube_video)
        from yt_dlp.networking.impersonate import ImpersonateTarget
        opts["impersonate"] = ImpersonateTarget.from_str("chrome-136")
    except Exception:  # noqa: BLE001
        pass
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return list((info or {}).get("entries") or [])


def list_videos(ref: str, session: dict, *, limit: int = FULL_MAX_ITEMS, extract: Extract = _extract_flat) -> list[dict]:
    """Toàn bộ video của kênh (mới nhất trước, bỏ trùng). Phân loại lỗi: EmptyChannel / PrivateChannel / RefreshableError."""
    url, user = channel_url(ref), username_of(ref)
    jar = write_cookiejar(session["cookies"])
    try:
        entries = extract(url, jar, session.get("user_agent"), session.get("proxy") or None, limit)
    except Exception as exc:  # noqa: BLE001 — phân loại theo thông điệp thật của yt-dlp
        kind = classify_error(exc)
        if kind == "empty":
            raise EmptyChannel from exc
        if kind == "private":
            raise PrivateChannel from exc
        raise RefreshableError(str(exc)) from exc
    finally:
        try:
            os.remove(jar)
        except OSError:
            pass
    out: dict[str, dict] = {}
    for e in entries:
        v = entry_to_video(e or {}, user)
        if v and v["video_id"] not in out:
            out[v["video_id"]] = v
    return list(out.values())


# ── Phiên cookie lưu bền (theo proxy) ────────────────────────────────────────────────────

def _proxy_key(proxy: str | None) -> str:
    return proxy or "direct"


def load_session(proxy: str | None, path: Path = SESSION_FILE, now: Callable[[], float] = time.time) -> tuple[dict | None, bool]:
    """`(session, is_fresh)`; phiên được lưu RIÊNG cho từng proxy (cookie gắn với IP lúc lấy)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")).get("sessions", {}).get(_proxy_key(proxy))
    except (OSError, ValueError):
        return None, False
    if not data or not data.get("cookies"):
        return None, False
    return data, (now() - float(data.get("saved_at") or 0)) < FRESH_SESSION_S


def save_session(proxy: str | None, session: dict, path: Path = SESSION_FILE, now: Callable[[], float] = time.time) -> None:
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        root = {}
    root.setdefault("sessions", {})[_proxy_key(proxy)] = {**session, "saved_at": now()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(root, ensure_ascii=False), encoding="utf-8")


# ── Điều phối ────────────────────────────────────────────────────────────────────────────

def fetch_links(ref: str, *, chrome_path: str, proxies: list[str], limit: int = FULL_MAX_ITEMS,
                extract: Extract = _extract_flat, get_session: Callable[[str, str | None], dict] = browser_cookie.fetch_tiktok_session,
                session_path: Path = SESSION_FILE) -> dict:
    """Lấy toàn bộ link video của kênh. `{videos, via, browser_opens, kind}` hoặc ném `TikTokError`.

    `via`: proxy đã dùng (đã che mật khẩu) hoặc "trực tiếp"; `kind`: "ok" | "empty"."""
    candidates: list[str | None] = list(proxies) or [None]
    opens = 0
    errors: list[str] = []
    for proxy in candidates:
        label = mask_proxy(proxy) if proxy else "trực tiếp (IP máy này)"
        session, fresh = load_session(proxy, session_path)
        just_fetched = False
        for attempt in range(2):
            if session is None:
                try:
                    session = get_session(chrome_path, proxy)
                except browser_cookie.BrowserError as exc:
                    errors.append(f"{label}: {exc}")
                    break
                opens += 1
                just_fetched, fresh = True, True
                save_session(proxy, session, session_path)
            try:
                videos = list_videos(ref, session, limit=limit, extract=extract)
                return {"videos": videos, "via": label, "browser_opens": opens, "kind": "ok"}
            except EmptyChannel:
                return {"videos": [], "via": label, "browser_opens": opens, "kind": "empty"}
            except PrivateChannel as exc:
                raise TikTokError("Kênh TikTok riêng tư hoặc không tồn tại — kiểm tra lại link.") from exc
            except RefreshableError as exc:
                errors.append(f"{label}: {str(exc)[:160]}")
                if just_fetched or fresh or attempt == 1:   # cookie còn mới mà vẫn lỗi => vấn đề ở IP/proxy: đổi proxy, không mở lại trình duyệt
                    break
                session = None                              # cookie cũ: làm mới đúng một lần rồi thử lại
    hint = ("TikTok đang chặn IP này (không có phiên hợp lệ) — thêm proxy khác ở Cấu hình rồi thử lại" if not proxies
            else "mọi proxy đã thử đều bị TikTok chặn hoặc lỗi — thêm/đổi proxy ở Cấu hình (nên dùng proxy dân cư) rồi thử lại")
    raise TikTokError(f"Không lấy được danh sách video ({hint}). Chi tiết: " + " | ".join(errors[-3:]) if errors
                      else f"Không lấy được danh sách video ({hint})")


def export_links(channel_label: str, videos: list[dict], export_dir: Path = EXPORT_DIR) -> str:
    """Ghi mỗi link một dòng ra file .txt (mới nhất trước); trả đường dẫn."""
    export_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-@]+", "_", channel_label)[:80] or "tiktok"
    path = export_dir / f"{safe}.txt"
    path.write_text("\n".join(v["url"] for v in videos) + ("\n" if videos else ""), encoding="utf-8")
    return str(path)


def run_fetch(cmd: dict, *, settings_path: Path = SETTINGS_FILE, **kw) -> dict:
    """Việc của nút "Lấy link video": lấy link -> lưu DB cục bộ -> xuất .txt. Lỗi trả `{"error": ...}` (không ném)."""
    channel_id = str(cmd.get("channel_id") or "")
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except (OSError, ValueError):
        settings = {}
    _, proxies = load_pools(str(settings_path))
    try:
        result = fetch_links(str(cmd.get("url") or ""), chrome_path=str(settings.get("chrome_path") or ""), proxies=proxies,
                             limit=int(cmd.get("limit") or FULL_MAX_ITEMS), **kw)
    except TikTokError as exc:
        return {"error": str(exc)}
    except browser_cookie.BrowserError as exc:
        return {"error": str(exc)}
    conn = channels_db._get_conn()
    try:
        saved = channels_db.act_save_channel_videos(conn, {"channel_id": channel_id, "videos": result["videos"]})
        videos = channels_db.act_list_channel_videos(conn, {"channel_id": channel_id})["videos"]
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()
    export = export_links(username_of(str(cmd.get("url") or "")) or channel_id, videos)
    return {"ok": True, "found": len(result["videos"]), "added": saved["added"], "total": saved["total"], "kind": result["kind"],
            "via": result["via"], "browser_opens": result["browser_opens"], "export": export}


def main() -> None:
    from system_api import use_utf8_stdio

    use_utf8_stdio()
    if len(sys.argv) < 2 or sys.argv[1] != "fetch":
        print("usage: tiktok_links.py fetch  (stdin JSON {channel_id, url, limit?})", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(run_fetch(json.loads(sys.stdin.read() or "{}")), ensure_ascii=False))


if __name__ == "__main__":
    main()
