from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

import httpx

try:
    from curl_cffi.requests import AsyncSession as _CffiAsyncSession  # type: ignore
    _HAS_CURL_CFFI = True
except Exception:  # noqa: BLE001
    _HAS_CURL_CFFI = False

from .core.exceptions.http import BadRequestException
from .core.settings import get_settings

try:
    # check_facebook.py chèn thư mục checker/ vào sys.path trước khi import package này.
    from account_headers import fingerprint_headers
except Exception:  # noqa: BLE001 — fallback nếu chạy độc lập ngoài runner
    def fingerprint_headers(user_agent: str = "", fingerprint_json: str = "") -> dict:  # type: ignore
        return {}

try:
    from proxy_utils import proxy_dict
except Exception:  # noqa: BLE001 — fallback nếu chạy độc lập ngoài runner
    # Fallback PHẢI giữ nguyên hành vi (kể cả ép socks5h): trả None sẽ âm thầm bỏ proxy
    # → request đi thẳng từ IP server, đúng thứ account-bundle sinh ra để tránh.
    def proxy_dict(proxy: str | None) -> dict[str, str] | None:  # type: ignore
        if not proxy:
            return None
        p = "socks5h://" + proxy[len("socks5://"):] if proxy.startswith("socks5://") else proxy
        return {"http": p, "https": p}

logger = logging.getLogger(__name__)

FACEBOOK_HOME = "https://www.facebook.com/"

REACTION_NAME_MAP = {
    "Thích": "like",
    "Yêu thích": "love",
    "Thương thương": "care",
    "Haha": "haha",
    "Wow": "wow",
    "Buồn": "sad",
    "Phẫn nộ": "angry",
    "Like": "like",
    "Love": "love",
    "Care": "care",
    "Sad": "sad",
    "Angry": "angry",
}


@dataclass
class FetchResult:
    url: str
    status_code: int | None
    html_text: str
    attempts: list[tuple[str, int]]


def _browser_headers(user_agent: str = "", fingerprint: str = "") -> dict[str, str]:
    """Build browser headers cho request FB.

    user_agent/fingerprint: account-bundle của CHÍNH cookie đang dùng (TaoThaoClaw,
    lưu trong cột cookies.user_agent/fingerprint) — truyền tường minh thay vì đọc
    ngầm qua env, để rõ ràng cookie nào dùng đúng bộ UA/fingerprint nào (quan trọng
    khi 1 process xoay vòng nhiều cookie — xem check_interlink_profile.py).

    Cookie CHƯA có account-bundle (chưa sync/cũ) → dùng UA mặc định — bump lên
    Chrome 146 (khớp curl_cffi impersonate="chrome146" đang dùng để fetch, tránh
    UA/TLS-fingerprint lệch phiên bản dễ bị FB soft-block).
    """
    headers = {
        "user-agent": user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "vi,en-US;q=0.9,en;q=0.8",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "referer": FACEBOOK_HOME,
    }
    # Merge sec-ch-ua + accept-language theo account-bundle (khi có user_agent/fingerprint).
    if user_agent or fingerprint:
        headers.update(fingerprint_headers(user_agent, fingerprint))
    return headers


async def _fetch_html(
    cookies: dict[str, str],
    url: str,
    proxy: str | None = None,
    user_agent: str = "",
    fingerprint: str = "",
) -> FetchResult:
    proxies = proxy_dict(proxy)
    headers = _browser_headers(user_agent, fingerprint)
    if _HAS_CURL_CFFI:
        async with _CffiAsyncSession(impersonate="chrome146") as session:
            response = await session.get(
                url, headers=headers, cookies=cookies,
                proxies=proxies, allow_redirects=True, timeout=30,
            )
    else:
        async with httpx.AsyncClient(
            follow_redirects=True, max_redirects=20, timeout=30, cookies=cookies, proxy=proxy,
        ) as client:
            response = await client.get(url, headers=headers)
    return FetchResult(
        url=str(response.url),
        status_code=response.status_code,
        html_text=response.text,
        attempts=[(url, response.status_code)],
    )


async def _fetch_first_supported_html(
    cookies: dict[str, str],
    urls: list[str],
    proxy: str | None = None,
    user_agent: str = "",
    fingerprint: str = "",
) -> FetchResult:
    attempts: list[tuple[str, int]] = []
    last_result = FetchResult(url=urls[-1] if urls else "", status_code=None, html_text="", attempts=[])
    proxies = proxy_dict(proxy)
    headers = _browser_headers(user_agent, fingerprint)

    if _HAS_CURL_CFFI:
        async with _CffiAsyncSession(impersonate="chrome146") as session:
            for url in urls:
                response = await session.get(
                    url, headers=headers, cookies=cookies,
                    proxies=proxies, allow_redirects=True, timeout=30,
                )
                attempts.append((url, response.status_code))
                last_result = FetchResult(
                    url=str(response.url),
                    status_code=response.status_code,
                    html_text=response.text,
                    attempts=attempts,
                )
                if (
                    response.status_code == 200
                    and response.text
                    and not _is_unsupported_html(response.text)
                ):
                    return last_result
                logger.info("Facebook fetch HTTP %s for %s", response.status_code, url)
    else:
        async with httpx.AsyncClient(
            follow_redirects=True, max_redirects=20, timeout=30, cookies=cookies, proxy=proxy,
        ) as client:
            for url in urls:
                response = await client.get(url, headers=headers)
                attempts.append((url, response.status_code))
                last_result = FetchResult(
                    url=str(response.url),
                    status_code=response.status_code,
                    html_text=response.text,
                    attempts=attempts,
                )
                if (
                    response.status_code == 200
                    and response.text
                    and not _is_unsupported_html(response.text)
                ):
                    return last_result
                logger.info("Facebook fetch HTTP %s for %s", response.status_code, url)

    last_result.attempts = attempts
    return last_result


def _parse_cookies(cookie_string: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in cookie_string.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            cookies[key] = value
    return cookies


def _parse_reel_url(reel_url: str) -> str:
    parsed = urlparse(reel_url)

    match = re.search(r"/reel/(\d+)", parsed.path)
    if match:
        return match.group(1)

    query = parse_qs(parsed.query)
    if query.get("v"):
        return query["v"][0]

    match = re.search(r"/videos/(\d+)", parsed.path)
    if match:
        return match.group(1)

    # /{vanity}/videos/{title-slug}/{id}/ — FB chèn slug tiêu đề giữa "videos/" và
    # ID số khi share reel/video kèm caption dài. Verified thực nghiệm 2026-07-06.
    match = re.search(r"/videos/[^/]+/(\d+)", parsed.path)
    if match:
        return match.group(1)

    # story.php?story_fbid=X&id=Y — FB redirect share reel/video sang dạng story
    # cũ. story_fbid thường CHÍNH LÀ id của story/video (khớp `_reel_json_nodes`
    # tìm theo node.id/videoId/video_id) — verified thực nghiệm 2026-07-06: 2/8
    # reel share link mẫu redirect dạng này, cả 2 đều fail "Could not extract reel
    # ID" vì thiếu fallback này (path /reel/, /videos/ và query v= đều không khớp).
    if query.get("story_fbid"):
        return query["story_fbid"][0]

    return ""


def _first_match(text: str, patterns: list[str], flags: int = 0) -> str:
    for pat in patterns:
        match = re.search(pat, text, flags)
        if match:
            return match.group(1)
    return ""


def _first_int(text: str, patterns: list[str]) -> int:
    value = _first_match(text, patterns)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _meta_content(html_text: str, keys: list[str]) -> str:
    for key in keys:
        patterns = [
            rf'<meta[^>]+property=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(key)}["\']',
            rf'<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(key)}["\']',
        ]
        value = _first_match(html_text, patterns, flags=re.IGNORECASE)
        if value:
            return html.unescape(value)
    return ""


def _clean_text(value: str) -> str:
    if not value:
        return ""
    value = html.unescape(value).replace("\\/", "/")
    if "\\" in value:
        try:
            value = json.loads(f'"{value}"')
        except json.JSONDecodeError:
            value = value.replace("\\u003C", "<").replace("\\u003E", ">")
    return html.unescape(value).strip()


def _json_script_payloads(html_text: str) -> list[Any]:
    payloads: list[Any] = []
    for raw_payload in re.findall(
        r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        try:
            payloads.append(json.loads(html.unescape(raw_payload)))
        except json.JSONDecodeError:
            logger.debug("Skipping invalid JSON script payload", exc_info=True)
    return payloads


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json(item)


def _all_json_nodes(html_text: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for payload in _json_script_payloads(html_text):
        nodes.extend(_walk_json(payload))
    return nodes


def _reel_json_nodes(html_text: str, reel_id: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in _all_json_nodes(html_text):
        if str(node.get("id") or node.get("videoId") or node.get("video_id") or "") != reel_id:
            continue
        identity = id(node)
        if identity not in seen:
            nodes.append(node)
            seen.add(identity)
    return nodes


def _nested_get(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_text_field(nodes: list[dict[str, Any]], paths: list[tuple[str, ...]]) -> str:
    for node in nodes:
        for path in paths:
            value = _nested_get(node, path)
            if isinstance(value, str) and value.strip():
                return _clean_text(value)
    return ""


def _first_json_int(nodes: list[dict[str, Any]], paths: list[tuple[str, ...]]) -> int:
    for node in nodes:
        for path in paths:
            value = _nested_get(node, path)
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                try:
                    return int(value)
                except ValueError:
                    continue
    return 0


def _best_video_url(nodes: list[dict[str, Any]]) -> str:
    candidates: list[tuple[int, int, str]] = []
    for node in nodes:
        delivery = _nested_get(
            node,
            ("videoDeliveryResponseFragment", "videoDeliveryResponseResult", "progressive_urls"),
        )
        if isinstance(delivery, list):
            for item in delivery:
                if not isinstance(item, dict):
                    continue
                url = item.get("progressive_url")
                if not isinstance(url, str) or not url:
                    continue
                quality = str(_nested_get(item, ("metadata", "quality")) or "").upper()
                quality_score = 2 if quality == "HD" else 1 if quality else 0
                candidates.append((quality_score, int(item.get("height") or 0), url))

        extensions = _nested_get(node, ("extensions", "all_video_dash_prefetch_representations"))
        if isinstance(extensions, list):
            for group in extensions:
                for item in group.get("representations", []) if isinstance(group, dict) else []:
                    if not isinstance(item, dict):
                        continue
                    url = item.get("base_url")
                    if isinstance(url, str) and url:
                        candidates.append((1, int(item.get("height") or 0), url))

    if not candidates:
        return ""
    return _clean_text(max(candidates, key=lambda item: (item[0], item[1]))[2])


def _owner_value(nodes: list[dict[str, Any]], field: str, exclude_id: str = "") -> str:
    """Lấy `field` (name/id/url/profile_url) của chủ bài từ node["owner"] (hoặc
    node["owner"]["owner_as_page"], hoặc creation_story actors).

    exclude_id: ID chủ cookie — bỏ CẢ owner dict nếu owner["id"] trùng exclude_id
    (không chỉ riêng field đang lấy), tránh vừa loại đúng "id" nhưng vẫn lấy nhầm
    "name"/"url" của cùng owner đó ở lượt gọi khác (xem docstring `_cookie_self_id`).
    """
    for node in nodes:
        owner = _nested_get(node, ("owner",))
        if isinstance(owner, dict) and not (exclude_id and str(owner.get("id") or "") == exclude_id):
            value = owner.get(field)
            if isinstance(value, str) and value.strip():
                return _clean_text(value)
            owner_as_page = owner.get("owner_as_page")
            if isinstance(owner_as_page, dict):
                value2 = owner_as_page.get(field)
                if isinstance(value2, str) and value2.strip():
                    return _clean_text(value2)

        actors = _nested_get(node, ("creation_story", "comet_sections", "actor_photo", "story", "actors"))
        if isinstance(actors, list):
            for actor in actors:
                if isinstance(actor, dict) and actor.get(field):
                    if exclude_id and str(actor.get("id") or "") == exclude_id:
                        continue
                    return _clean_text(str(actor[field]))
    return ""


def _post_story_id(html_text: str) -> str:
    return _clean_text(
        _first_match(
            html_text,
            [
                r'"storyID":"([^"]+)"',
                r'"story_id":"([^"]+)"',
            ],
        )
    )


def _post_json_nodes(html_text: str, post_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_nodes = _all_json_nodes(html_text)
    story_id = _post_story_id(html_text)
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()

    def add(node: dict[str, Any]) -> None:
        identity = id(node)
        if identity not in seen:
            selected.append(node)
            seen.add(identity)

    if story_id:
        for node in all_nodes:
            if node.get("id") == story_id:
                add(node)

    normalized_path = urlparse(post_url).path.rstrip("/")
    for node in all_nodes:
        node_url = str(node.get("wwwURL") or node.get("permalink_url") or node.get("url") or "")
        if normalized_path and urlparse(node_url).path.rstrip("/") == normalized_path:
            add(node)

    # photo.php?fbid=X — story_id không nhúng cho dạng URL ảnh cũ này, và path-match
    # ở trên không khớp (photo.php không có wwwURL/permalink_url trỏ về chính nó).
    # FB nhúng data ảnh dưới dạng node {"__typename":"Photo","id":"<fbid>",...} —
    # KHÔNG dùng field "post_id" như post thường. Code cũ match theo post_id==fbid
    # (sai field — node Photo thật không có "post_id") nên KHÔNG BAO GIỜ khớp, luôn
    # rơi vào node rác không liên quan → extract rỗng dù ảnh còn sống thật. Verified
    # thực nghiệm 2026-07-08 (link photo.php user xác nhận còn live nhưng báo lỗi
    # "Could not extract post fields"): node Photo thật có id==fbid +
    # __isMedia=="Photo", chứa đủ owner/feedback/creation_story/image. Gate thêm
    # __typename/__isMedia=="Photo" để không lỡ khớp node khác có id trùng ngẫu nhiên.
    fbid = (parse_qs(urlparse(post_url).query).get("fbid") or [""])[0]
    if fbid:
        for node in all_nodes:
            if str(node.get("id") or "") == fbid and (
                node.get("__typename") == "Photo" or node.get("__isMedia") == "Photo"
            ):
                add(node)

    # story.php?story_fbid=X&id=Y — permalink dạng cũ (pre-vanity-URL, hay gặp khi resolve
    # /share/p/... ra), giống hệt bug photo.php?fbid= ở trên: story_id nhúng trong HTML lẫn
    # URL-path match đều không khớp (story.php không có wwwURL/permalink_url trỏ về chính nó
    # — path chỉ là "/story.php", còn story_fbid nằm ở query). FB nhúng data dưới node
    # {"id":"<story_fbid>",...} — match theo id + có tín hiệu post thật (actors/message/
    # comet_sections, cùng điều kiện _feed_post_nodes) để tránh khớp nhầm node rác trùng id.
    # _canonical_post_url() chỉ cứu được trường hợp có ĐỦ cả story_fbid lẫn id; block này
    # bắt phần còn lại. Port từ bản checkview mới, nơi ~80 submission FB thật báo "Could not
    # extract post fields" dù link vẫn mở/xem được thủ công, toàn bộ đều dạng story.php.
    story_fbid = (parse_qs(urlparse(post_url).query).get("story_fbid") or [""])[0]
    if story_fbid:
        for node in all_nodes:
            if str(node.get("id") or "") == story_fbid and (
                node.get("actors") or node.get("message") or node.get("comet_sections")
            ):
                add(node)

    # KHÔNG fallback sang node bất kỳ có post_id+message/actors khi story_id lẫn
    # URL-path đều không khớp — mirror bug đã fix bên Instagram (ig_scraper.py
    # _selected_media_nodes): trang lỗi/fallback-feed vẫn nhúng post khác (feed
    # gợi ý) thoả điều kiện này, khiến scraper trả nhầm chủ bài. Không xác minh
    # được → coi là fail thật, để caller báo lỗi thay vì đoán bừa.
    return selected, all_nodes


def _feed_post_nodes(html_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_nodes = _all_json_nodes(html_text)
    selected: list[dict[str, Any]] = []
    seen_post_ids: set[str] = set()
    for node in all_nodes:
        post_id = str(node.get("post_id") or "")
        if not post_id or post_id in seen_post_ids:
            continue
        if not (node.get("actors") or node.get("message") or node.get("comet_sections")):
            continue
        selected.append(node)
        seen_post_ids.add(post_id)

    return selected, all_nodes


def _cookie_self_id(cookies: dict[str, str]) -> str:
    """c_user trong cookie đã parse = numeric ID của chính chủ cookie đang đăng nhập.

    Dùng để loại trừ khỏi kết quả actor/owning_profile — HTML nhúng lại ID viewer
    ở nhiều nơi (nav, chat, comment-composer mặc định...) có thể lẫn vào các node
    "actors"/"owning_profile" nếu cấu trúc trang trùng field name. Cùng nguyên tắc
    đã áp dụng cho `check_interlink_profile.py` (`_cookie_self_id`).
    """
    return str(cookies.get("c_user") or "").strip()


def _first_actor(nodes: list[dict[str, Any]], exclude_id: str = "") -> dict[str, Any]:
    for node in nodes:
        actors = node.get("actors")
        if isinstance(actors, list):
            for actor in actors:
                if isinstance(actor, dict) and actor.get("id") and str(actor["id"]) != exclude_id:
                    return actor
        for child in _walk_json(node):
            actors = child.get("actors")
            if isinstance(actors, list):
                for actor in actors:
                    if isinstance(actor, dict) and actor.get("id") and str(actor["id"]) != exclude_id:
                        return actor
    return {}


def _find_owning_profile(data: Any, exclude_id: str = "") -> dict[str, Any]:
    """Recursively search any JSON structure for the first owning_profile dict.

    FB embeds the post author as owning_profile at varying depths depending on
    the GraphQL shape (timeline feed, group feed, permalink, etc.).
    Port từ seeding/machine/external/scrapers/fb_scraper.py.

    exclude_id: ID chủ cookie — bỏ qua owning_profile trùng ID này, tìm tiếp (xem
    docstring `_cookie_self_id`).
    """
    if isinstance(data, dict):
        if "owning_profile" in data and isinstance(data["owning_profile"], dict):
            candidate = data["owning_profile"]
            if not exclude_id or str(candidate.get("id") or "") != exclude_id:
                return candidate
        for value in data.values():
            result = _find_owning_profile(value, exclude_id)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_owning_profile(item, exclude_id)
            if result:
                return result
    return {}


def _post_timestamp(nodes: list[dict[str, Any]]) -> int:
    for node in nodes:
        for child in _walk_json(node):
            value = child.get("creation_time")
            if isinstance(value, int):
                return value
    return 0


def _post_text(nodes: list[dict[str, Any]]) -> str:
    value = _first_text_field(
        nodes,
        [
            ("message", "text"),
            ("comet_sections", "content", "story", "comet_sections", "message", "story", "message", "text"),
            ("comet_sections", "message", "story", "message", "text"),
            ("comet_sections", "message_container", "story", "message", "text"),
        ],
    )
    if value:
        return value

    for node in nodes:
        for child in _walk_json(node):
            message = child.get("message")
            if isinstance(message, dict) and isinstance(message.get("text"), str):
                return _clean_text(message["text"])
    return ""


def _post_media(nodes: list[dict[str, Any]]) -> tuple[list[str], str, str, str]:
    images: list[str] = []
    seen_images: set[str] = set()
    video = ""
    video_thumbnail = ""
    link = ""

    for node in nodes:
        if isinstance(node.get("ghl_mocked_encrypted_link"), str):
            link = link or _clean_text(node["ghl_mocked_encrypted_link"])
        for child in _walk_json(node):
            image = _nested_get(child, ("photo_image", "uri"))
            if isinstance(image, str) and image and image not in seen_images:
                images.append(_clean_text(image))
                seen_images.add(image)

            if child.get("__typename") == "Video":
                video = video or _clean_text(
                    str(child.get("playable_url_quality_hd") or child.get("playable_url") or "")
                )
                video_thumbnail = video_thumbnail or _clean_text(
                    str(
                        _nested_get(child, ("thumbnailImage", "uri"))
                        or _nested_get(child, ("preferred_thumbnail", "image", "uri"))
                        or ""
                    )
                )

            child_url = child.get("url")
            if not link and isinstance(child_url, str) and "l.facebook.com/l.php" in child_url:
                link = _clean_text(child_url)

    return images, video, video_thumbnail, link


def _feedback_id(nodes: list[dict[str, Any]]) -> str:
    for node in nodes:
        feedback = node.get("feedback")
        if isinstance(feedback, dict) and isinstance(feedback.get("id"), str):
            return feedback["id"]
    return ""


def _post_feedback_counts(feedback_id: str, all_nodes: list[dict[str, Any]]) -> tuple[int, int, int, dict[str, int]]:
    likes = 0
    comments = 0
    shares = 0
    reactions: dict[str, int] = {}
    if not feedback_id:
        return likes, comments, shares, reactions

    for node in all_nodes:
        if node.get("id") != feedback_id:
            continue

        reaction_count = _nested_get(node, ("reaction_count", "count"))
        if isinstance(reaction_count, int):
            likes = max(likes, reaction_count)

        comment_count = _nested_get(node, ("comment_rendering_instance", "comments", "total_count"))
        if isinstance(comment_count, int):
            comments = max(comments, comment_count)

        share_count = _nested_get(node, ("share_count", "count"))
        if isinstance(share_count, int):
            shares = max(shares, share_count)
        reshare_count = _nested_get(node, ("reshares", "count"))
        if isinstance(reshare_count, int):
            shares = max(shares, reshare_count)

        edges = _nested_get(node, ("top_reactions", "edges"))
        if isinstance(edges, list):
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                name = _nested_get(edge, ("node", "localized_name"))
                count = edge.get("reaction_count")
                if isinstance(name, str) and isinstance(count, int):
                    clean_name = _clean_text(name)
                    reactions[REACTION_NAME_MAP.get(clean_name, clean_name.lower())] = count

    return likes, comments, shares, reactions


def _post_dict_from_nodes(
    post_nodes: list[dict[str, Any]],
    all_nodes: list[dict[str, Any]],
    source_url: str,
    exclude_id: str = "",
) -> dict[str, Any]:
    post_id = _first_text_field(post_nodes, [("post_id",)])
    story_id = _first_text_field(post_nodes, [("id",)])
    text = _post_text(post_nodes)
    actor = _first_actor(post_nodes, exclude_id)
    if not actor.get("name"):
        # Fallback: FB embeds author as owning_profile at varying depths — CHỈ tìm
        # trong post_nodes đã xác minh (story_id/URL-path khớp đúng bài). KHÔNG mở
        # rộng sang all_nodes (toàn trang) — sẽ bắt nhầm owning_profile của bài
        # khác (feed gợi ý) khi không tìm thấy trong node đã xác minh.
        op = _find_owning_profile(post_nodes, exclude_id)
        if op.get("name"):
            actor = op
    timestamp = _post_timestamp(post_nodes)
    time_str = (
        datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat() if timestamp else None
    )
    images, video, video_thumbnail, link = _post_media(post_nodes)
    feedback_id = _feedback_id(post_nodes)
    likes, comments, shares, reactions = _post_feedback_counts(feedback_id, all_nodes)
    canonical_url = _first_text_field(
        post_nodes,
        [
            ("wwwURL",),
            ("permalink_url",),
            ("url",),
        ],
    ) or source_url

    return {
        "post_id": post_id,
        "story_id": story_id,
        "post_url": canonical_url,
        "resolved_from": source_url if source_url != canonical_url else None,
        "text": text,
        "time": time_str,
        "timestamp": timestamp or None,
        "username": _clean_text(str(actor.get("name") or "")),
        "user_id": str(actor.get("id") or ""),
        "user_url": _clean_text(str(actor.get("url") or actor.get("profile_url") or "")),
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "reactions": reactions,
        "images": images,
        "video": video,
        "video_thumbnail": video_thumbnail,
        "link": link,
        "available": True,
    }


_TRACKING_PARAMS = frozenset({
    "app", "mibextid", "fs", "ref", "s", "sk", "__tn__", "__cft__",
    "acontext", "rdid", "share_url", "paipv", "eav",
})


def _strip_tracking_params(url: str) -> str:
    """Xoá tracking/redirect params khỏi FB URL — giữ lại chỉ params định danh nội dung.

    Các params như ?app=fbl, ?mibextid=... khiến FB serve trang redirect/app-link
    thay vì HTML post thật → scraper đọc nhầm "không khả dụng" cho bài còn live.
    Params cần giữ: fbid, story_fbid, id, v (video id), set (album).
    """
    parsed = urlparse(url)
    if not parsed.query:
        return url
    from urllib.parse import urlencode
    keep_params = {k: v for k, v in parse_qs(parsed.query).items()
                   if k.lower() not in _TRACKING_PARAMS}
    new_query = urlencode({k: v[0] for k, v in keep_params.items()}) if keep_params else ""
    return parsed._replace(query=new_query).geturl()


def _normalize_post_url(url: str) -> str:
    """Convert story/mobile URLs to canonical /{user_id}/posts/{pfbid}/ form.

    Handles:
    - m.facebook.com/... → www.facebook.com/...
    - story.php?story_fbid={pfbid}&id={user_id} → /{user_id}/posts/{pfbid}/
    - Strips tracking params (app=fbl, mibextid, ...) that cause FB to serve
      app-redirect pages instead of real post HTML (false "không khả dụng").
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    # Normalize mobile host → www
    if host.startswith("m.") and host.endswith("facebook.com"):
        url = url.replace(f"//{parsed.hostname}", "//www.facebook.com", 1)
        parsed = urlparse(url)

    # story.php?story_fbid={pfbid}&id={user_id} → canonical post URL
    if parsed.path.rstrip("/") == "/story.php":
        query = parse_qs(parsed.query)
        if query.get("story_fbid") and query.get("id"):
            pfbid = query["story_fbid"][0]
            user_id = query["id"][0]
            return f"https://www.facebook.com/{user_id}/posts/{pfbid}/"

    # Strip tracking params khỏi mọi URL post còn lại
    return _strip_tracking_params(url)


def _candidate_reel_urls(reel_url: str, reel_id: str) -> list[str]:
    candidates: list[str] = []
    for candidate in [
        f"https://www.facebook.com/watch/?v={reel_id}" if reel_id else "",
        f"https://m.facebook.com/watch/?v={reel_id}" if reel_id else "",
        f"https://www.facebook.com/watch/?ref=search&v={reel_id}" if reel_id else "",
        f"https://mbasic.facebook.com/watch/?v={reel_id}" if reel_id else "",
        reel_url,
    ]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _is_unsupported_html(html_text: str) -> bool:
    lowered = html_text.lower()
    markers = [
        "trình duyệt này không hỗ trợ facebook",
        "unsupported-interstitial",
        "browser isn't supported",
        "download one of these browsers",
    ]
    return any(marker in lowered for marker in markers)


def _is_checkpoint_or_login(url: str) -> bool:
    """True khi FB redirect về trang checkpoint/login.php — session bị challenge THẬT
    (cần re-login). Phân biệt với /login/?next= là content-restriction (xem _is_login_wall)."""
    low = (url or "").lower()
    return "/checkpoint/" in low or "/login.php" in low


def _is_login_wall(url: str) -> bool:
    """True khi FB redirect về /login/?next=... — ambiguous: có thể content private/deleted
    HOẶC rate-limit tạm thời. Cookie KHÔNG nhất thiết chết — worker dùng cooldown thay vì disable."""
    low = (url or "").lower()
    return "/login/" in low and "/login.php" not in low and "/checkpoint/" not in low


# Title markers FB sets when redirecting a dead reel/video to the "Discover popular videos" feed.
# Without detecting this, the scraper would extract stats of the first recommended reel — different per cookie.
# Verified empirically on 2026-05-26 against deleted reel id 929602366614804.
_FALLBACK_FEED_TITLE_MARKERS = (
    "khám phá các video phổ biến",
    "discover popular videos",
)


def _page_title(html_text: str) -> str:
    match = re.search(r"<title>([^<]+)</title>", html_text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _is_fallback_feed_html(html_text: str) -> bool:
    """True when FB served the personalized 'Discover popular videos' feed instead of the target reel/post."""
    title_lower = _page_title(html_text).lower()
    return any(marker in title_lower for marker in _FALLBACK_FEED_TITLE_MARKERS)


# FB renders CometErrorRoot.react khi bài post/group post KHÔNG XEM ĐƯỢC (chủ giới
# hạn quyền xem, đã xoá, hoặc account đang dùng cookie không nằm trong nhóm được
# xem) — không phải lỗi scraper. Verified thực nghiệm 2026-07-04: node rootView.props
# của trang lỗi thật chứa {title: "Bạn hiện không xem được nội dung này", body:
# "Lỗi này thường do chủ sở hữu chỉ chia sẻ nội dung với một nhóm nhỏ..."}.
#
# BUG đã fix 2026-07-06: bản đầu chỉ check substring '"CometErrorRoot.react"' —
# tên component này nằm trong bootloader's "markComponentsAsImmediate"/"require"
# manifest của HẦU HẾT MỌI trang Facebook (kể cả reel/post đang sống bình thường,
# chỉ là liệt kê component CÓ THỂ được lazy-load, không phải đang render). Verified
# thực nghiệm: reel share/r/18sitiufW8 (video_id trích xuất được, sống thật) vẫn
# khớp substring này → bị báo nhầm "không khả dụng" dù data hoàn toàn extract được.
# Fix: chỉ match text TIÊU ĐỀ THẬT của trang lỗi (rootView.props đã render), không
# phải tên component trong manifest.
_ACCESS_DENIED_TEXT_MARKERS = (
    "Bạn hiện không xem được nội dung này",
    "This content isn't available right now",
    "This content isn't available",
)


def _is_access_denied_html(html_text: str) -> bool:
    return any(marker in html_text for marker in _ACCESS_DENIED_TEXT_MARKERS)


# ── Authoritative view-count via GraphQL ─────────────────────────────────────
# FB lazy-loads reel/video view counts through the CometVideoHomeNewPermalinkHeroUnitQuery
# GraphQL operation. The static HTML carries `video_view_count_renderer:null`, so the old
# regex fallback grabbed the FIRST `view_count` in the page — which belongs to a recommended
# video in the /watch/ feed (garbage, different per cookie/load). We replay the operation and
# read the count from the UFI2ViewCountRenderer whose feedback.associated_video.id == reel_id,
# making the number trustworthy. Verified empirically on 2026-06-01: feed-claimed views match
# the GraphQL video_view_count exactly across high- and zero-view videos.
_HERO_QUERY_NAME = "CometVideoHomeNewPermalinkHeroUnitQuery"
_GRAPHQL_URL = "https://www.facebook.com/api/graphql/"


def _match_braces(text: str, start: int) -> str:
    """Return the balanced ``{...}`` substring beginning at index ``start`` (must point at '{')."""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""


def _extract_session_tokens(html_text: str) -> dict[str, str]:
    def grab(pattern: str) -> str:
        match = re.search(pattern, html_text)
        return match.group(1) if match else ""

    return {
        "fb_dtsg": grab(r'\["DTSGInitialData",\[\],\{"token":"([^"]+)"'),
        "lsd": grab(r'\["LSD",\[\],\{"token":"([^"]+)"'),
        "user": grab(r'"USER_ID":"(\d+)"'),
        "rev": grab(r'"__spin_r":(\d+)'),
        "spin_b": grab(r'"__spin_b":"([^"]+)"'),
        "spin_t": grab(r'"__spin_t":(\d+)'),
        "jazoest": grab(r'jazoest=(\d+)'),
    }


def _extract_hero_query(html_text: str, reel_id: str) -> tuple[str, dict[str, Any]] | None:
    """Harvest the (doc_id, variables) FB embedded for the permalink hero-unit query.

    Harvested fresh per request so it survives FB's periodic doc_id rotation.
    """
    marker = re.search(rf'"queryName":"{_HERO_QUERY_NAME}"', html_text)
    if not marker:
        return None
    window = html_text[max(0, marker.start() - 5000) : marker.start()]
    doc_ids = re.findall(r'"queryID":"(\d{15,})"', window)
    var_start = window.rfind('"variables":{')
    if not doc_ids or var_start < 0:
        return None
    raw = _match_braces(window, var_start + len('"variables":'))
    try:
        variables = json.loads(raw)
    except json.JSONDecodeError:
        return None
    # The embedded copy already targets this video, but force it to be defensive.
    variables["videoID"] = reel_id
    variables["videoIDStr"] = reel_id
    return doc_ids[-1], variables


def _viewcount_from_graphql_text(text: str, reel_id: str) -> dict[str, int] | None:
    """Parse a (possibly streamed, multi-line) GraphQL response for the target video's counts.

    Keyed on ``feedback.associated_video.id == reel_id`` so recommended-video counts in the
    same response are never mistaken for the target's.
    """
    payloads: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payloads.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    counts: dict[str, int] = {}
    feedback_id = ""
    for payload in payloads:
        for node in _walk_json(payload):
            if node.get("__typename") != "UFI2ViewCountRenderer":
                continue
            feedback = node.get("feedback")
            if not isinstance(feedback, dict):
                continue
            if str((feedback.get("associated_video") or {}).get("id")) != reel_id:
                continue
            feedback_id = str(feedback.get("id") or "")
            if isinstance(feedback.get("video_view_count"), int):
                counts["video_view_count"] = feedback["video_view_count"]
            if isinstance(feedback.get("play_count"), int):
                counts["play_count"] = feedback["play_count"]

    if not counts and not feedback_id:
        return None

    # Reactions / comments / shares live on other slices of the same feedback node.
    if feedback_id:
        for payload in payloads:
            for node in _walk_json(payload):
                if str(node.get("id")) != feedback_id:
                    continue
                reactions = _nested_get(node, ("reaction_count", "count"))
                if isinstance(reactions, int):
                    counts["reaction_count"] = max(counts.get("reaction_count", 0), reactions)
                comment_total = node.get("total_comment_count")
                if isinstance(comment_total, int):
                    counts["comment_count"] = max(counts.get("comment_count", 0), comment_total)
                share_total = _nested_get(node, ("reshares", "count"))
                if not isinstance(share_total, int):
                    share_total = _nested_get(node, ("share_count", "count"))
                if isinstance(share_total, int):
                    counts["share_count"] = max(counts.get("share_count", 0), share_total)

    return counts or None


async def _fetch_reel_view_count(
    cookies: dict[str, str],
    reel_id: str,
    html_text: str,
    proxy: str | None,
    user_agent: str = "",
    fingerprint: str = "",
    asbd_id: str = "",
) -> dict[str, int] | None:
    """Replay FB's hero-unit GraphQL op to read authoritative view/play/reaction counts.

    Returns None when the session is logged out (no fb_dtsg) or the op cannot be harvested —
    callers then fall back to (non-polluting) JSON-node extraction.
    """
    tokens = _extract_session_tokens(html_text)
    hero = _extract_hero_query(html_text, reel_id) if tokens["fb_dtsg"] else None
    if hero is None:
        # The first fetched candidate may not be the permalink page that embeds the hero op.
        try:
            refetch = await _fetch_html(
                cookies, f"https://www.facebook.com/watch/?v={reel_id}", proxy=proxy,
                user_agent=user_agent, fingerprint=fingerprint,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("Facebook view-count refetch failed for %s: %s", reel_id, exc)
            return None
        html_text = refetch.html_text
        tokens = _extract_session_tokens(html_text)
        hero = _extract_hero_query(html_text, reel_id) if tokens["fb_dtsg"] else None
    if hero is None:
        return None
    doc_id, variables = hero

    body = {
        "av": tokens["user"],
        "__user": tokens["user"],
        "__a": "1",
        "__comet_req": "15",
        "dpr": "1",
        "fb_dtsg": tokens["fb_dtsg"],
        "jazoest": tokens["jazoest"],
        "lsd": tokens["lsd"],
        "__spin_r": tokens["rev"],
        "__spin_b": tokens["spin_b"],
        "__spin_t": tokens["spin_t"],
        "__rev": tokens["rev"],
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": _HERO_QUERY_NAME,
        "variables": json.dumps(variables, separators=(",", ":")),
        "server_timestamps": "true",
        "doc_id": doc_id,
    }
    headers = {
        "user-agent": _browser_headers(user_agent, fingerprint)["user-agent"],
        "content-type": "application/x-www-form-urlencoded",
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "x-fb-friendly-name": _HERO_QUERY_NAME,
        "x-fb-lsd": tokens["lsd"],
        # Ưu tiên giá trị bắt từ browser thật (ClawHelper, account-bundle) — asbd-id gắn
        # với build web app của FB, đổi khi FB deploy bản mới. "129477" chỉ là fallback
        # cho cookie chưa được đẩy lại qua ClawHelper bản mới (chưa có asbd_id lưu sẵn).
        "x-asbd-id": asbd_id or "129477",
        "origin": "https://www.facebook.com",
        "referer": f"https://www.facebook.com/watch/?v={reel_id}",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    # Merge sec-ch-ua/platform/mobile + accept-language THEO đúng fingerprint account
    # (ghi đè accept-language hardcode ở trên) — thiếu bước này khiến request GraphQL
    # lệch fingerprint so với HTML request cùng account, dễ bị FB soft-block.
    if user_agent or fingerprint:
        headers.update(fingerprint_headers(user_agent, fingerprint))
    try:
        proxies = proxy_dict(proxy)
        if _HAS_CURL_CFFI:
            async with _CffiAsyncSession(impersonate="chrome146") as session:
                response = await session.post(
                    _GRAPHQL_URL, data=body, headers=headers,
                    cookies=cookies, proxies=proxies, allow_redirects=True, timeout=30,
                )
        else:
            async with httpx.AsyncClient(
                follow_redirects=True, max_redirects=20, timeout=30, cookies=cookies, proxy=proxy
            ) as client:
                response = await client.post(_GRAPHQL_URL, data=body, headers=headers)
        return _viewcount_from_graphql_text(response.text, reel_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("Facebook view-count GraphQL failed for %s: %s", reel_id, exc)
        return None


class FBScraperService:
    REELS_DOC_ID = ""

    @staticmethod
    def _load_cookies() -> dict[str, str]:
        return _parse_cookies(get_settings().FB_COOKIES)

    @classmethod
    async def get_reel_by_url(
        cls,
        reel_url: str,
        debug_html: str | None = None,
        proxy: str | None = None,
        user_agent: str = "",
        fingerprint: str = "",
        asbd_id: str = "",
    ) -> dict[str, Any]:
        cookies = cls._load_cookies()
        self_id = _cookie_self_id(cookies)
        detected_id = _parse_reel_url(reel_url)
        fetch = await _fetch_first_supported_html(
            cookies,
            _candidate_reel_urls(reel_url, detected_id),
            proxy=proxy,
            user_agent=user_agent,
            fingerprint=fingerprint,
        )
        # Cookie bị FB thách thức thật → /checkpoint/ hoặc /login.php → 'checkpoint:' prefix
        # để worker cooldown/disable cookie. /login/?next= là content restriction (bài xoá/private
        # hoặc rate-limit tạm) → 'login-wall:' prefix, worker KHÔNG disable cookie, chỉ cooldown.
        if _is_checkpoint_or_login(fetch.url):
            raise BadRequestException(
                detail=f"checkpoint: FB chặn cookie (redirect {fetch.url[:100]})"
            )
        if _is_login_wall(fetch.url):
            raise BadRequestException(
                detail=f"login-wall: Reel không khả dụng hoặc FB rate-limit tạm thời (redirect {fetch.url[:100]})"
            )
        html_text = fetch.html_text

        # Guard: when input is an opaque /share/ or fb.watch/ link, FB occasionally soft-redirects
        # dead shares to a recommended /{user}/videos/{id} page belonging to an unrelated account.
        # The HTML is real (no fallback-feed title), but the data belongs to the wrong reel —
        # confirmed by same URL returning different usernames/views per cookie session. Only
        # reject when the final URL settled at a non-canonical content path (e.g. /{user}/videos/).
        # Login walls (/login/ redirect) are NOT this bug — cookie just isn't authorized for that
        # share; let it fall through to fail naturally as "Could not extract reel ID".
        input_lower = reel_url.lower()
        is_opaque_input = ("/share/" in input_lower) or ("fb.watch/" in input_lower)
        if is_opaque_input and not detected_id:
            final_parsed = urlparse(fetch.url)
            final_path = final_parsed.path.lower()
            looks_like_user_videos = re.match(r"^/[^/]+/videos/\d+", final_path) is not None
            if looks_like_user_videos:
                raise BadRequestException(
                    detail=(
                        f"Share link không khả dụng (FB redirect tới recommendation): "
                        f"{reel_url} -> {fetch.url}"
                    )
                )

        reel_id = detected_id or _parse_reel_url(fetch.url)
        if not reel_id:
            reel_id = _first_match(
                html_text,
                [
                    r'"videoID":"(\d+)"',
                    r'"video_id":"(\d+)"',
                    r'"story_fbid":"(\d+)"',
                    r'"reel_id":"(\d+)"',
                ],
            )

        if not reel_id:
            raise BadRequestException(detail=f"Could not extract reel ID from: {reel_url}")

        if debug_html and html_text:
            Path(debug_html).write_text(html_text, encoding="utf-8")

        # Guard: when the reel is deleted/unavailable, FB silently returns the personalized
        # "Discover popular videos" feed. Without this, downstream regex fallbacks would pick
        # metrics from the first recommended reel — yielding different fake numbers per cookie.
        if _is_fallback_feed_html(html_text) or _is_access_denied_html(html_text):
            raise BadRequestException(
                detail=f"Reel không khả dụng (đã bị xóa, bị ẩn hoặc giới hạn quyền xem): {reel_url}"
            )

        json_nodes = _reel_json_nodes(html_text, str(reel_id))

        # Authoritative counts: FB defers view/play/reaction counts behind a GraphQL op, so the
        # static HTML has `video_view_count_renderer:null`. Replay that op (keyed to this exact
        # video id) instead of regex-scraping the page, which would pick a recommended video's
        # numbers. Returns None for logged-out cookies → falls back to JSON-node extraction.
        gql_counts = await _fetch_reel_view_count(
            cookies, str(reel_id), html_text, proxy,
            user_agent=user_agent, fingerprint=fingerprint, asbd_id=asbd_id,
        )

        video_url = _best_video_url(json_nodes) or _clean_text(
            _first_match(
                html_text,
                [
                    r'"browser_native_hd_url":"([^"]+)"',
                    r'"browser_native_sd_url":"([^"]+)"',
                    r'"playable_url_quality_hd":"([^"]+)"',
                    r'"playable_url":"([^"]+)"',
                ],
            )
            or _meta_content(html_text, ["og:video", "og:video:url", "og:video:secure_url"])
        )

        thumbnail = _first_text_field(
            json_nodes,
            [
                ("preferred_thumbnail", "image", "uri"),
                ("preferred_thumbnail", "image", "url"),
                ("thumbnailImage", "uri"),
                ("image", "uri"),
            ],
        ) or _clean_text(
            _first_match(
                html_text,
                [
                    r'"preferred_thumbnail":\{.*?"uri":"([^"]+)"',
                    r'"thumbnailImage":\{.*?"uri":"([^"]+)"',
                    r'"image":\{.*?"uri":"([^"]+)"',
                ],
                flags=re.DOTALL,
            )
            or _meta_content(html_text, ["og:image", "twitter:image"])
        )

        caption = _first_text_field(
            json_nodes,
            [
                ("savable_description", "text"),
                ("title", "text"),
                ("name",),
                ("creation_story", "message", "text"),
                ("creation_story", "comet_sections", "message", "story", "message", "text"),
            ],
        ) or _clean_text(
            _first_match(
                html_text,
                [
                    r'"message":\{"text":"([^"]*?)"',
                    r'"title":\{"text":"([^"]*?)"',
                    r'"creation_story":\{.*?"message":\{"text":"([^"]*?)"',
                ],
                flags=re.DOTALL,
            )
            or _meta_content(html_text, ["og:description", "description"])
        )

        # og:title bị loại khỏi fallback vì nó trả caption video, không phải tên chủ reel.
        #
        # BUG đã fix 2026-07-06: từng có fallback regex mù r'"owner":\{.*?"name":"..."'
        # chạy trên TOÀN html_text (không scope theo reel_id) — khớp nhầm 1 object
        # "owner" bất kỳ ở nơi khác trong trang (vd metadata bundle JS), verified thực
        # nghiệm: trả "WAMediaWasmWorkerBundle" (tên module JS, không phải username thật)
        # làm chủ reel. Chỉ tin json_nodes đã scope theo reel_id (`_owner_value`) +
        # `_find_owning_profile(json_nodes)` bên dưới — cùng nguyên tắc đã áp dụng cho
        # post/group (`_post_dict_from_nodes`) và Instagram (`_selected_media_nodes`).
        # exclude_id=self_id: loại trừ ID chủ cookie đang dùng để check — tránh lấy
        # nhầm identity của viewer thay vì chủ reel thật (xem docstring `_cookie_self_id`).
        username = _owner_value(json_nodes, "name", self_id)
        user_id = _owner_value(json_nodes, "id", self_id)
        user_url = _clean_text(
            _owner_value(json_nodes, "url", self_id) or _owner_value(json_nodes, "profile_url", self_id)
        )

        if not username:
            # CHỈ tìm trong json_nodes đã scope theo reel_id — KHÔNG mở rộng sang
            # _all_json_nodes(html_text) (toàn trang), sẽ bắt nhầm owning_profile
            # của reel khác (reels-tray/gợi ý) nhúng cùng trang (cùng lớp bug đã
            # fix ở _post_dict_from_nodes/_post_json_nodes phía trên).
            op = _find_owning_profile(json_nodes, self_id)
            username = _clean_text(str(op.get("name") or ""))
            user_id = user_id or str(op.get("id") or "")
            user_url = user_url or _clean_text(str(op.get("url") or ""))

        # user_url tiếp tục được tinh chỉnh (ưu tiên vanity URL thật) ở "Nguồn 1"/"Nguồn 2"
        # bên dưới — filter chặt theo user_id, không dùng regex mù trên toàn html_text.

        _CONTENT_SEGS = ("/reel/", "/posts/", "/videos/", "/share/", "/stories/", "story.php", "permalink.php")
        _SLUG_SKIP = frozenset({"pages","groups","events","watch","reel","reels","share","people","profile.php","hashtag","stories"})

        def _has_vanity(u: str) -> bool:
            """True chỉ khi u là profile URL với vanity slug thật (không phải content URL hay numeric)."""
            if not u:
                return False
            ul = u.lower()
            if any(p in ul for p in _CONTENT_SEGS):
                return False  # content URL (video/reel/post page), không phải profile
            if "profile.php" in ul:
                return False
            m = re.search(r"facebook\.com/([^/?#]+)", u, re.I)
            if not m:
                return False
            slug = m.group(1)
            return bool(slug) and not slug.isdigit() and slug.lower() not in _SLUG_SKIP

        # Nguồn 1: actor/author nodes lọc theo user_id
        # (vanity nằm trong actor.url / author.url ở group/share context, không phải owner.url)
        if user_id and not _has_vanity(user_url):
            uid_str = str(user_id)
            for _node in _all_json_nodes(html_text):
                for _child in _walk_json(_node):
                    for _key in ("actor", "author", "story_author"):
                        _val = _child.get(_key)
                        if (
                            isinstance(_val, dict)
                            and str(_val.get("id", "")) == uid_str
                            and _val.get("url")
                            and "profile.php" not in str(_val["url"])
                        ):
                            user_url = _clean_text(str(_val["url"]))
                            break
                    if _has_vanity(user_url):
                        break
                if _has_vanity(user_url):
                    break

        # Nguồn 2: fetch.url có dạng /{vanity}/videos/{id} sau redirect từ share link
        if user_id and not _has_vanity(user_url):
            _vm = re.search(r"facebook\.com/([^/?#]+)/videos/", fetch.url, re.I)
            if _vm:
                _slug = _vm.group(1)
                _SKIP = {"watch", "share", "reel", "reels", "groups", "pages", "events"}
                if not _slug.isdigit() and _slug.lower() not in _SKIP:
                    user_url = f"https://www.facebook.com/{_slug}"

        # Nguồn 3: numeric fallback
        if not user_url and user_id:
            user_url = f"https://www.facebook.com/{user_id}"

        timestamp = _first_json_int(
            json_nodes,
            [
                ("publish_time",),
                ("creation_story", "comet_sections", "metadata", "story", "creation_time"),
            ],
        ) or _first_int(
            html_text,
            [
                r'"(?:creation_time|publish_time|published_time)":(\d+)',
                r'"creation_timestamp":(\d+)',
            ],
        )
        time_str = (
            datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat() if timestamp else None
        )

        # GraphQL (keyed to this exact video) is authoritative. The old whole-page count
        # regexes are intentionally dropped — they matched the FIRST occurrence in the page,
        # which on a /watch/ feed belongs to a recommended video, yielding different fake
        # numbers per cookie/load. Only fall back to id-scoped JSON nodes when GraphQL is
        # unavailable (e.g. logged-out cookie).
        # Reels display "plays", so report play_count as the primary number (matches the FB UI
        # and the original extraction order); fall back to video_view_count, then to id-scoped
        # JSON nodes. Logged-out sessions get the count server-rendered into the static HTML, so
        # the JSON-node fallback (keyed to this video id) stays correct without the GraphQL op.
        gql = gql_counts or {}
        plays = gql.get("play_count") or gql.get("video_view_count")
        if not plays:
            plays = _first_json_int(
                json_nodes,
                [
                    ("feedback", "video_view_count_renderer", "feedback", "play_count"),
                    ("feedback", "video_view_count_renderer", "feedback", "video_view_count"),
                ],
            )
        play_count = gql.get("play_count", 0)
        video_view_count = gql.get("video_view_count", 0)
        likes = gql.get("reaction_count")
        if likes is None:
            likes = _first_json_int(
                json_nodes,
                [
                    ("feedback", "reaction_count", "count"),
                ],
            )
        comments = gql.get("comment_count")
        if comments is None:
            comments = _first_json_int(
                json_nodes,
                [
                    ("feedback", "total_comment_count"),
                    ("feedback", "comment_rendering_instance", "comments", "total_count"),
                ],
            )
        shares = gql.get("share_count")
        if shares is None:
            shares = _first_json_int(
                json_nodes,
                [
                    ("feedback", "reshares", "count"),
                    ("feedback", "share_count", "count"),
                ],
            )

        resolved_from = None
        canonical_url = f"https://www.facebook.com/reel/{reel_id}"
        if reel_url != canonical_url:
            resolved_from = reel_url

        if not any([video_url, thumbnail, caption, username, user_id, timestamp, plays, likes, comments, shares]):
            # KHÔNG được nhắc chữ "checkpoint" ở đây — đây chỉ là DIỄN GIẢI PHỎNG ĐOÁN
            # (HTTP status thực tế vẫn 200, không phải redirect checkpoint thật, đã bị
            # `_is_access_denied_html`/resolve_share_url bắt riêng ở nơi khác nếu đúng
            # là checkpoint). worker.ts's isCheckpointError() match RẤT rộng theo regex
            # /checkpoint/i trên toàn bộ message — có chữ "checkpoint" ở đây là auto
            # disable NGAY cookie đang dùng, dù nguyên nhân thật có thể chỉ là reel bị
            # xoá/riêng tư hoặc lỗi extract không liên quan tới tài khoản. Verified thực
            # nghiệm 2026-07-08: 1 reel lỗi kiểu này đốt sạch cả pool cookie FB trong vài
            # giây (mỗi cookie thử đều dính raise này → bị disable oan hàng loạt).
            attempts_str = ", ".join(f"{status} {url}" for url, status in fetch.attempts)
            last_fetch_url = fetch.attempts[-1][0] if fetch.attempts else reel_url
            detail = (
                f"Facebook did not return reel data (last HTTP {fetch.status_code or 'unknown'} for {last_fetch_url}). "
                "Reel may be deleted/private, hoặc scraper extract thiếu field. "
                f"Attempts: {attempts_str}"
            )
            raise BadRequestException(detail=detail)

        return {
            "reel_id": str(reel_id),
            "reel_url": canonical_url,
            "resolved_from": resolved_from,
            "video_url": video_url,
            "thumbnail": thumbnail,
            "caption": caption,
            "time": time_str,
            "timestamp": timestamp or None,
            "username": username,
            "user_id": user_id,
            "user_url": user_url,
            "plays": plays,
            "play_count": play_count,
            "video_view_count": video_view_count,
            "likes": likes,
            "comments": comments,
            "shares": shares,
        }

    @classmethod
    async def get_profile(cls, page: str) -> dict[str, Any]:
        raise NotImplementedError("The profile crawler has not been restored in source form yet.")

    @classmethod
    async def get_posts(
        cls,
        page: str,
        pages: int = 2,
        timeout: int = 30,
        extra_info: bool = False,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("The post crawler has not been restored in source form yet.")

    @classmethod
    async def get_reels(cls, page: str, pages: int = 2) -> list[dict[str, Any]]:
        raise NotImplementedError("The reels list crawler has not been restored in source form yet.")

    @classmethod
    async def get_post_by_url(
        cls,
        post_url: str,
        proxy: str | None = None,
        user_agent: str = "",
        fingerprint: str = "",
    ) -> dict[str, Any]:
        # Normalize story.php / mobile URLs before any processing.
        # story.php?story_fbid={pfbid}&id={user_id} → /{user_id}/posts/{pfbid}/
        post_url = _normalize_post_url(post_url)

        # Guard: homepage (empty path) or profile root (single path segment like /username) —
        # scraping these returns the FIRST feed post, a different random post per cookie/load.
        # Reject unless URL has a content path (2+ segments) or a query-based permalink.
        parsed = urlparse(post_url)
        query = parse_qs(parsed.query)
        path_segments = [s for s in parsed.path.strip("/").split("/") if s]
        if len(path_segments) <= 1 and not (query.get("story_fbid") or query.get("fbid")):
            raise BadRequestException(
                detail=f"Không phải URL post hợp lệ (trang chủ hoặc trang profile): {post_url}"
            )

        cookies = cls._load_cookies()
        self_id = _cookie_self_id(cookies)
        fetch = await _fetch_html(
            cookies, post_url, proxy=proxy, user_agent=user_agent, fingerprint=fingerprint,
        )

        # Checkpoint thật (/checkpoint/ hoặc /login.php) → cookie cần re-login.
        # Login-wall (/login/?next=) → content restriction hoặc rate-limit tạm, không phải cookie chết.
        if _is_checkpoint_or_login(fetch.url):
            raise BadRequestException(
                detail=f"checkpoint: FB chặn cookie (redirect {fetch.url[:100]})"
            )
        if _is_login_wall(fetch.url):
            raise BadRequestException(
                detail=f"login-wall: Post không khả dụng hoặc FB rate-limit tạm thời (redirect {fetch.url[:100]})"
            )

        if fetch.status_code != 200 or not fetch.html_text or _is_unsupported_html(fetch.html_text):
            raise BadRequestException(
                detail=(
                    f"Facebook did not return post data (HTTP {fetch.status_code or 'unknown'} for {post_url}). "
                    "Check FB_COOKIES or try opening the original post URL in a logged-in browser."
                )
            )

        if _is_fallback_feed_html(fetch.html_text) or _is_access_denied_html(fetch.html_text):
            raise BadRequestException(
                detail=f"Post không khả dụng (đã bị xóa, bị ẩn hoặc giới hạn quyền xem): {post_url}"
            )

        post_nodes, all_nodes = _post_json_nodes(fetch.html_text, fetch.url)
        if not post_nodes:
            raise BadRequestException(detail=f"Could not extract post data from: {post_url}")

        result = _post_dict_from_nodes(post_nodes, all_nodes, fetch.url, self_id)
        result["story_id"] = _post_story_id(fetch.html_text) or result["story_id"]

        if not any(
            [
                result["post_id"],
                result["story_id"],
                result["text"],
                result["username"],
                result["images"],
                result["video"],
                result["timestamp"],
            ]
        ):
            raise BadRequestException(detail=f"Could not extract post fields from: {post_url}")

        return result

    @classmethod
    async def get_group_user_posts(
        cls,
        url: str,
        user_agent: str = "",
        fingerprint: str = "",
    ) -> list[dict[str, Any]]:
        cookies = cls._load_cookies()
        self_id = _cookie_self_id(cookies)
        fetch = await _fetch_html(cookies, url, user_agent=user_agent, fingerprint=fingerprint)

        if fetch.status_code != 200 or not fetch.html_text or _is_unsupported_html(fetch.html_text):
            raise BadRequestException(
                detail=(
                    f"Facebook did not return group-user data (HTTP {fetch.status_code} for {url}). "
                    "Check FB_COOKIES or group visibility."
                )
            )

        post_nodes, all_nodes = _feed_post_nodes(fetch.html_text)
        return [_post_dict_from_nodes([node], all_nodes, fetch.url, self_id) for node in post_nodes]
