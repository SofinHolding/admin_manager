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
import channel_listing
import channels_db
import tiktok_links

EXPORT_DIR = Path(channels_db.ROOT_DIR) / ".dev" / "video-links"


def export_links(channel_label: str, videos: list[dict], export_dir: Path | None = None) -> str:
    """Ghi mỗi link một dòng ra file .txt (mới nhất trước); trả đường dẫn."""
    export_dir = export_dir or EXPORT_DIR
    export_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-@]+", "_", channel_label)[:80] or "channel"
    path = export_dir / f"{safe}.txt"
    path.write_text("\n".join(v["url"] for v in videos) + ("\n" if videos else ""), encoding="utf-8")
    return str(path)


def run_fetch(cmd: dict, *, settings_path: Path | None = None) -> dict:
    """Lấy link -> lưu DB cục bộ -> xuất .txt. Mọi lỗi trả `{"error": ...}` (không ném)."""
    channel_id, platform, ref = str(cmd.get("channel_id") or ""), str(cmd.get("platform") or ""), str(cmd.get("url") or "")
    creds = channel_collect.load_credentials(settings_path or channel_collect.DEFAULT_SETTINGS)
    try:
        if platform == "youtube":
            result = channel_listing.list_youtube(ref, creds)
        elif platform == "tiktok":
            result = channel_listing.list_tiktok(ref, creds)
        else:
            raise channel_listing.ListingError(f"Nền tảng {platform or '?'} chưa hỗ trợ lấy toàn bộ link video")
    except channel_listing.ListingError as exc:
        return {"error": str(exc)}
    conn = channels_db._get_conn()
    try:
        saved = channels_db.act_save_channel_videos(conn, {"channel_id": channel_id, "videos": result["videos"], "complete": True})
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
            label = channel_listing.youtube_channel_url(ref).rsplit("/", 1)[-1].lstrip("@")
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
