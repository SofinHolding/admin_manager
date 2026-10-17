#!/usr/bin/env python3
"""
video_links.py — nút "Lấy toàn bộ link video" của tab Người dùng & Kênh (YouTube + TikTok): lấy hết video của kênh -> lưu DB cục bộ
(`channel_videos`) -> xuất file .txt (mới nhất trước).

  youtube : YouTube Data API (playlist uploads + thống kê, xoay nhiều key); không có key thì yt-dlp (chỉ link, không tiêu đề/lượt xem)
  tiktok  : yt-dlp lặp qua cổng chống bot (`tiktok_links.py`)

CLI (Electron gọi, dữ liệu qua stdin):
  echo '{"platform":"tiktok","channel_id":"<id kênh trong DB>","url":"https://www.tiktok.com/@abc"}' | python video_links.py fetch
Kết quả: `{"ok":true,"found":n,"added":n,"total":n,"kind":"ok|empty","via":...,"attempts":n,"export":path}` hoặc `{"error":...}`.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import channel_collect
import channels_db
import tiktok_links
from checkers import youtube as yt_checker

EXPORT_DIR = Path(channels_db.ROOT_DIR) / ".dev" / "video-links"
YOUTUBE_MAX = 5000        # trần số video một kênh YouTube (200 đơn vị quota)


class LinksError(Exception):
    """Lỗi thân thiện, hiển thị thẳng cho người dùng."""


def export_links(channel_label: str, videos: list[dict], export_dir: Path | None = None) -> str:
    """Ghi mỗi link một dòng ra file .txt (mới nhất trước); trả đường dẫn."""
    export_dir = export_dir or EXPORT_DIR
    export_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-@]+", "_", channel_label)[:80] or "channel"
    path = export_dir / f"{safe}.txt"
    path.write_text("\n".join(v["url"] for v in videos) + ("\n" if videos else ""), encoding="utf-8")
    return str(path)


def fetch_youtube(ref: str, creds: channel_collect.Credentials, limit: int = YOUTUBE_MAX) -> dict:
    """Toàn bộ video của kênh YouTube: `{videos, via, attempts, kind}` hoặc ném `LinksError`."""
    if creds.youtube_keys:
        api = yt_checker.YouTubeApi(creds.key_pool)
        try:
            channel = api.resolve_channel(ref)
            ids = api.recent_video_ids(channel["uploads"], limit) if channel["uploads"] else []
            stats = api.video_stats(ids) if ids else {}
        except ValueError as exc:
            raise LinksError(str(exc)) from exc
        except (yt_checker.NoKeyAvailable, yt_checker.YouTubeApiError) as exc:
            raise LinksError(str(exc)) from exc
        finally:
            api.close()
        videos = [{"video_id": i, "url": f"https://www.youtube.com/watch?v={i}", "title": stats.get(i, {}).get("title", ""),
                   "published_at": str(stats.get(i, {}).get("published_at") or ""), "views": stats.get(i, {}).get("views")} for i in ids]
        return {"videos": videos, "via": "YouTube Data API", "attempts": 1, "kind": "ok" if videos else "empty"}
    proxy = creds.proxy_pool.pick() if creds.proxies else None
    try:
        ids = yt_checker.list_channel_ytdlp(channel_collect.youtube_channel_url(ref), limit, proxy)
    except ValueError as exc:
        raise LinksError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        if "does not have a videos tab" in str(exc):
            return {"videos": [], "via": "yt-dlp", "attempts": 1, "kind": "empty"}
        raise LinksError(f"yt-dlp không liệt kê được video: {str(exc)[:200]} (thêm YouTube API key ở Cấu hình để ổn định hơn)") from exc
    videos = [{"video_id": i, "url": f"https://www.youtube.com/watch?v={i}", "title": "", "published_at": "", "views": None} for i in ids]
    return {"videos": videos, "via": "yt-dlp", "attempts": 1, "kind": "ok" if videos else "empty"}


def fetch_tiktok(ref: str, creds: channel_collect.Credentials) -> dict:
    try:
        return tiktok_links.fetch_links(ref, proxies=creds.proxies)
    except tiktok_links.TikTokError as exc:
        raise LinksError(str(exc)) from exc


def run_fetch(cmd: dict, *, settings_path: Path | None = None) -> dict:
    """Lấy link -> lưu DB cục bộ -> xuất .txt. Mọi lỗi trả `{"error": ...}` (không ném)."""
    channel_id, platform, ref = str(cmd.get("channel_id") or ""), str(cmd.get("platform") or ""), str(cmd.get("url") or "")
    creds = channel_collect.load_credentials(settings_path or channel_collect.DEFAULT_SETTINGS)
    try:
        if platform == "youtube":
            result = fetch_youtube(ref, creds)
        elif platform == "tiktok":
            result = fetch_tiktok(ref, creds)
        else:
            raise LinksError(f"Nền tảng {platform or '?'} chưa hỗ trợ lấy toàn bộ link video")
    except LinksError as exc:
        return {"error": str(exc)}
    conn = channels_db._get_conn()
    try:
        saved = channels_db.act_save_channel_videos(conn, {"channel_id": channel_id, "videos": result["videos"]})
        videos = channels_db.act_list_channel_videos(conn, {"channel_id": channel_id})["videos"]
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()
    label = ""
    if platform == "tiktok":
        label = tiktok_links.username_of(ref)
    elif platform == "youtube":
        try:
            label = channel_collect.youtube_channel_url(ref).rsplit("/", 1)[-1].lstrip("@")
        except ValueError:
            pass
    export = export_links(label or channel_id, videos)
    return {"ok": True, "found": len(result["videos"]), "added": saved["added"], "total": saved["total"], "kind": result["kind"],
            "via": result["via"], "attempts": result["attempts"], "export": export}


def main() -> None:
    from system_api import use_utf8_stdio

    use_utf8_stdio()
    if len(sys.argv) < 2 or sys.argv[1] != "fetch":
        print("usage: video_links.py fetch  (stdin JSON {platform, channel_id, url})", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(run_fetch(json.loads(sys.stdin.read() or "{}")), ensure_ascii=False))


if __name__ == "__main__":
    main()
