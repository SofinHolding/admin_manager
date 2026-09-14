"""Công thức thống kê THUẦN — nguồn nghiệp vụ chuẩn dùng CHUNG cho Tool và Admin Viewer.

Vì sao module này tồn tại: trước đây `app/analytics/service.py` (Tool) và `admin-server/db.py`
(Admin) mỗi bên TỰ tính số liệu, và bản reimplement bên Admin lệch nhiều chỗ so với Tool. Tách toàn
bộ công thức ra đây, cả hai bên chỉ còn dựng input rồi gọi chung, thì không còn chỗ cho lệch.

RÀNG BUỘC THIẾT KẾ (đừng phá):
- THUẦN: chỉ `datetime` + stdlib. KHÔNG import gì trong `app/` (kể cả config/store). Nhờ vậy file
  này copy nguyên xi (byte-identical) sang `admin-server/stats_core.py` — admin-server là process
  triển khai độc lập, KHÔNG import được package `app/`. Test `test_stats_core_vendored_parity.py`
  canh hai bản luôn giống hệt nhau.
- Nhận input đã CHUẨN HOÁ (list/dict thuần). Mỗi bên tự dựng input từ kho của mình.

NGỮ NGHĨA `None` cho ngày trống (giữ NGUYÊN như Tool, đừng "ép 0 cho giống"):
- Trong chuỗi view theo ngày, `None` = "hôm đó CHƯA có dữ liệu" còn `0` = "có dữ liệu, không ai
  xem". Vẽ hai thứ giống nhau lên biểu đồ là đọc SAI số liệu (đường đứt đoạn vs tụt đáy). Vì là
  module thuần không có khái niệm "ép 0", ta chủ động trả `None` đúng chỗ Tool trả `None`.

Hình dạng input chuẩn hoá:
- fb_posts:     list[{"target","date","views","reactions","comments_count"}]
- yt_daily:     list[{"channel_id","date","views"}]
- channel_meta: list[{"id","title","country"}]           (kênh YouTube — tên + quốc gia)
- events:       list[{"kind","date","target"}]
- pairings:     list[{"target","enabled","group_id"}]
- groups:       list[{"id","enabled","country"}]
- fb_page_meta: list[{"target","name","country"}]         (country gán TRỰC TIẾP cho page, có thể "")
"""

from __future__ import annotations

from datetime import date as _date
from datetime import timedelta

KHAC = "Khác"
KIND_POST_OK = "post_ok"
_ALL_LABEL = "Tổng — cả hai nền tảng"


# ── Trục ngày ─────────────────────────────────────────────────────────────────

def days_between(date_from: str, date_to: str, *, max_days: int | None = 400) -> list[str]:
    """Danh sách ngày `YYYY-MM-DD` trong khoảng (inclusive).

    Sao chép NGUYÊN hành vi `app/repost/insights_store.days_between`: khoảng đảo ngược hoặc dài quá
    `max_days` → rỗng (KHÔNG tự đảo). Đây là trục ngày mà cả hai biểu đồ Tool dùng."""
    try:
        a, b = _date.fromisoformat(date_from), _date.fromisoformat(date_to)
    except (TypeError, ValueError):
        return []
    if b < a or (max_days is not None and (b - a).days > max_days):
        return []
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


# ── Nhóm reup ─────────────────────────────────────────────────────────────────

def cho_chay(group: dict | None) -> bool:
    """Nhóm có cho cặp bên trong chạy không — mirror `app/repost/group_store.cho_chay`.

    `None` (cặp không thuộc nhóm nào, hoặc nhóm đã bị xoá) → để cặp tự quyết → coi là CHẠY."""
    if not group:
        return True
    return group.get("enabled", True) is not False


# ── Bảng tra: target FB → quốc gia / tên ──────────────────────────────────────

def target_country_map(fb_page_meta: list[dict], pairings: list[dict],
                       groups: list[dict]) -> dict[str, str]:
    """target FB → quốc gia. Mirror `app/analytics/router._target_country_map`.

    1) country gán TRỰC TIẾP cho page/profile (`fb_page_meta[].country`) — chỉ tính khi KHÁC RỖNG,
       phủ MỌI nơi đăng kể cả cái không có cặp reup.
    2) fallback tag của group cho target CHƯA có country trực tiếp."""
    out: dict[str, str] = {}
    for m in fb_page_meta:
        target = (m.get("target") or "").strip()
        ctry = (m.get("country") or "").strip()
        if target and ctry:
            out[target] = ctry
    group_country = {g["id"]: (g.get("country") or "") for g in groups}
    for p in pairings:
        target = (p.get("target") or "").strip()
        if not target or out.get(target):
            continue
        gc = group_country.get((p.get("group_id") or "").strip(), "")
        if gc:
            out[target] = gc
    return out


def target_names(fb_page_meta: list[dict]) -> dict[str, str]:
    """target FB → tên page. Mirror phần `page_name` của `dashboard.owner_index`.

    `fb_page_meta[].name` đã được dựng theo đúng quy tắc `pg.name or id` nên tra thẳng."""
    out: dict[str, str] = {}
    for m in fb_page_meta:
        target = (m.get("target") or "").strip()
        if target:
            out[target] = m.get("name") or target
    return out


def country_filters(country: str, channel_meta: list[dict],
                    target_country: dict[str, str]) -> tuple[set[str] | None, set[str] | None]:
    """`(tập channel_id YT, tập target FB)` khớp quốc gia — mirror khối lọc ở router.

    `country` rỗng → `(None, None)` = KHÔNG lọc. So khớp không phân biệt hoa/thường."""
    if not country:
        return None, None
    low = country.lower()
    yt = {c["id"] for c in channel_meta if (c.get("country") or "").lower() == low}
    fb = {t for t, c in target_country.items() if c.lower() == low}
    return yt, fb


# ── Tổng hợp theo ngày (thuần) — mirror các reader trong store ─────────────────

def _fb_by_day(fb_posts: list[dict], days: list[str], targets: set[str] | None,
               field: str | None) -> dict[str, int | None]:
    """Tổng theo ngày từ chi tiết bài FB. `field=None` → ĐẾM bài; ngược lại cộng `field`.

    `None` cho ngày không có bài nào (mirror `insights_store.{views,reactions,comments,posts}_by_day`)."""
    out: dict[str, int | None] = {d: None for d in days}
    for p in fb_posts:
        if targets is not None and (p.get("target")) not in targets:
            continue
        day = p.get("date")
        if day not in out:
            continue
        if field is None:
            out[day] = (out[day] or 0) + 1
        else:
            v = p.get(field)
            if v is not None:
                out[day] = (out[day] or 0) + int(v)
    return out


def _yt_views_by_day(yt_daily: list[dict], days: list[str],
                     channels: set[str] | None) -> dict[str, int | None]:
    """Tổng view YT mỗi ngày. `None` cho ngày không có bản ghi (mirror yt `views_by_day`)."""
    out: dict[str, int | None] = {d: None for d in days}
    for rec in yt_daily:
        day = rec.get("date")
        if day not in out:
            continue
        if channels is not None and rec.get("channel_id") not in channels:
            continue
        v = rec.get("views")
        if v is None:
            continue
        out[day] = (out[day] or 0) + int(v)
    return out


def _fb_views_by_target_day(fb_posts: list[dict], days: list[str]) -> dict[str, dict[str, int]]:
    """{target: {date: views}} — mirror `insights_store.views_by_target_day`."""
    valid = set(days)
    out: dict[str, dict[str, int]] = {}
    for p in fb_posts:
        day = p.get("date")
        if day not in valid:
            continue
        target = p.get("target") or ""
        by_day = out.setdefault(target, {})
        by_day[day] = by_day.get(day, 0) + int(p.get("views") or 0)
    return out


def _yt_views_by_channel_day(yt_daily: list[dict], days: list[str]) -> dict[str, dict[str, int]]:
    """{channel_id: {date: views}} — mirror yt `views_by_channel_day` (bỏ ngày views None)."""
    valid = set(days)
    out: dict[str, dict[str, int]] = {}
    for rec in yt_daily:
        cid = rec.get("channel_id")
        day = rec.get("date")
        v = rec.get("views")
        if not cid or day not in valid or v is None:
            continue
        by_day = out.setdefault(cid, {})
        by_day[day] = by_day.get(day, 0) + int(v)
    return out


# ── Các chỉ số công khai ───────────────────────────────────────────────────────

def traffic_metric(date_from: str, date_to: str, *,
                   fb_posts: list[dict], yt_daily: list[dict],
                   events: list[dict] | None = None,
                   scope: str = "", country: str = "",
                   yt_filter: set[str] | None = None,
                   fb_filter: set[str] | None = None) -> dict:
    """Chuỗi theo ngày khớp contract `TrafficMetricSeries` — reproduce `service.traffic_metrics`.

    scope: '' = cả hai nền tảng | 'youtube' | 'facebook'.
    views None-aware (None khi cả yt/fb đều None theo scope). interactions/comments CHỈ từ Facebook
    (cột Business Suite). videos = SỐ BÀI Facebook mỗi ngày (fb_posts), fallback đếm event `post_ok`
    cho ngày chưa cào — YouTube KHÔNG cộng vào số video."""
    show_yt = scope in ("", "youtube")
    show_fb = scope in ("", "facebook")
    days = days_between(date_from, date_to)

    yt = _yt_views_by_day(yt_daily, days, yt_filter) if show_yt else {}
    fb = _fb_by_day(fb_posts, days, fb_filter, "views") if show_fb else {}
    fb_reactions = _fb_by_day(fb_posts, days, fb_filter, "reactions") if show_fb else {}
    fb_cmts = _fb_by_day(fb_posts, days, fb_filter, "comments_count") if show_fb else {}
    fb_posts_cnt = _fb_by_day(fb_posts, days, fb_filter, None) if show_fb else {}

    posted_count: dict[str, int] = {}
    if show_fb and events:
        for e in events:
            if e.get("kind") != KIND_POST_OK:
                continue
            t = e.get("target") or ""
            if fb_filter is not None and t not in fb_filter:
                continue
            d = e.get("date") or ""
            if d:
                posted_count[d] = posted_count.get(d, 0) + 1

    day_rows: list[dict] = []
    totals_views = 0
    totals_videos = 0
    for d in days:
        yt_v = yt.get(d)
        fb_v = fb.get(d)
        if show_yt and show_fb:
            views: int | None = ((yt_v or 0) + (fb_v or 0)) if (yt_v is not None or fb_v is not None) else None
        elif show_yt:
            views = yt_v
        else:
            views = fb_v
        fb_pd = fb_posts_cnt.get(d)
        vid_cnt = (fb_pd if fb_pd is not None else posted_count.get(d, 0)) if show_fb else 0
        react_v = (fb_reactions.get(d) or 0) if show_fb else 0
        cmt_v = (fb_cmts.get(d) or 0) if show_fb else 0
        day_rows.append({"date": d, "views": views, "interactions": react_v,
                         "comments": cmt_v, "videos": vid_cnt})
        if views is not None:
            totals_views += views
        totals_videos += vid_cnt

    if scope == "youtube":
        label = "YouTube"
    elif scope == "facebook":
        label = "Facebook"
    else:
        label = _ALL_LABEL
    if country:
        label = f"{label} · {country}"

    return {
        "scope": scope,
        "scope_label": label,
        "country": country,
        "days": day_rows,
        "totals": {
            "views": totals_views,
            "interactions": sum((fb_reactions.get(d) or 0) for d in days),
            "comments": sum((fb_cmts.get(d) or 0) for d in days),
            "videos": totals_videos,
        },
    }


def summary(date_from: str, date_to: str, *,
            fb_posts: list[dict], yt_daily: list[dict],
            events: list[dict] | None = None,
            prev_events: list[dict] | None = None,
            prev_from: str | None = None, prev_to: str | None = None,
            pairings: list[dict] | None = None, groups: list[dict] | None = None,
            yt_filter: set[str] | None = None,
            fb_filter: set[str] | None = None) -> dict:
    """Tóm tắt KPI khớp contract `AnalyticsSummary` — reproduce `service.summary`.

    total_views = tổng view FB+YT trong khoảng. videos_posted = số event `post_ok`.
    managed_channels = distinct target trong pairings. active_channels = distinct target có ít nhất
    1 pairing enabled=True và group `cho_chay` (cặp không thuộc nhóm nào VẪN tính active)."""
    days = days_between(date_from, date_to)
    yt_v = _yt_views_by_day(yt_daily, days, yt_filter)
    fb_v = _fb_by_day(fb_posts, days, fb_filter, "views")
    total_views = (sum(v for v in yt_v.values() if v)
                   + sum(v for v in fb_v.values() if v))

    total_views_prev = 0
    if prev_from and prev_to:
        pdays = days_between(prev_from, prev_to)
        yt_pv = _yt_views_by_day(yt_daily, pdays, yt_filter)
        fb_pv = _fb_by_day(fb_posts, pdays, fb_filter, "views")
        total_views_prev = (sum(v for v in yt_pv.values() if v)
                            + sum(v for v in fb_pv.values() if v))

    videos_posted = len([e for e in (events or []) if e.get("kind") == KIND_POST_OK])
    videos_posted_prev = len([e for e in (prev_events or []) if e.get("kind") == KIND_POST_OK])

    days_in_range = len(days)

    ps = pairings or []
    managed_channels = len({p["target"] for p in ps if p.get("target")})

    groups_by_id = {g["id"]: g for g in (groups or [])}
    active: set[str] = set()
    for p in ps:
        if not p.get("enabled"):
            continue
        target = (p.get("target") or "").strip()
        if not target:
            continue
        grp = groups_by_id.get(p.get("group_id")) if p.get("group_id") else None
        if cho_chay(grp):
            active.add(target)
    active_channels = len(active)

    return {
        "total_views": total_views,
        "total_views_prev": total_views_prev,
        "videos_posted": videos_posted,
        "videos_posted_prev": videos_posted_prev,
        "active_channels": active_channels,
        "managed_channels": managed_channels,
        "days_in_range": days_in_range,
    }


def top_channels(date_from: str, date_to: str, *,
                 fb_posts: list[dict], yt_daily: list[dict],
                 channel_meta: list[dict],
                 target_names: dict[str, str],
                 target_country: dict[str, str],
                 country: str = "", limit: int = 10) -> dict:
    """Top kênh YouTube + nơi đăng Facebook theo tổng view — reproduce `service.top_channels`.

    YT gom theo channel_id (tên/quốc gia từ `channel_meta`); FB gom theo target (tên từ
    `target_names`, quốc gia từ `target_country`). Sort view giảm dần, cắt `limit`. Shape
    `{"items": [...]}`."""
    items: list[dict] = []
    cntry_low = country.lower() if country else ""
    days = days_between(date_from, date_to)

    chan_meta_by_id = {c["id"]: c for c in channel_meta}
    yt_cbd = _yt_views_by_channel_day(yt_daily, days)
    for cid, day_views in yt_cbd.items():
        ch = chan_meta_by_id.get(cid) or {}
        ch_country = (ch.get("country") or "")
        if cntry_low and ch_country.lower() != cntry_low:
            continue
        items.append({
            "id": f"youtube:{cid}",
            "name": ch.get("title") or cid,
            "platform": "youtube",
            "country": ch_country,
            "views": sum(day_views.values()),
        })

    fb_tbd = _fb_views_by_target_day(fb_posts, days)
    for target, day_views in fb_tbd.items():
        tc = target_country.get(target) or ""
        if cntry_low and tc.lower() != cntry_low:
            continue
        items.append({
            "id": target,
            "name": target_names.get(target) or target,
            "platform": "facebook",
            "country": tc,
            "views": sum(day_views.values()),
        })

    items.sort(key=lambda x: x["views"], reverse=True)
    return {"items": items[:limit]}


def views_by_country(date_from: str, date_to: str, *,
                     fb_posts: list[dict], yt_daily: list[dict],
                     channel_meta: list[dict],
                     target_country: dict[str, str]) -> dict:
    """View mỗi ngày phân bổ theo quốc gia — reproduce `service.views_by_country`.

    YT: quốc gia từ `channel_meta[].country` (rỗng → "Khác"). FB: từ `target_country` (rỗng →
    "Khác"). `countries` sort theo bảng chữ cái. Shape `{countries, days, totals}`."""
    chan_country = {c["id"]: (c.get("country") or KHAC) for c in channel_meta}

    days = days_between(date_from, date_to)
    by_day: dict[str, dict[str, int]] = {d: {} for d in days}
    all_countries: set[str] = set()

    yt_cbd = _yt_views_by_channel_day(yt_daily, days)
    for cid, day_views in yt_cbd.items():
        cntry = chan_country.get(cid) or KHAC
        all_countries.add(cntry)
        for d, v in day_views.items():
            if d in by_day:
                by_day[d][cntry] = by_day[d].get(cntry, 0) + v

    fb_tbd = _fb_views_by_target_day(fb_posts, days)
    for target, day_views in fb_tbd.items():
        cntry = (target_country.get(target) or "") or KHAC
        all_countries.add(cntry)
        for d, v in day_views.items():
            if d in by_day:
                by_day[d][cntry] = by_day[d].get(cntry, 0) + v

    countries = sorted(all_countries)
    totals: dict[str, int] = {}
    for day_data in by_day.values():
        for cntry, v in day_data.items():
            totals[cntry] = totals.get(cntry, 0) + v

    return {
        "countries": countries,
        "days": [{"date": d, **by_day[d]} for d in days],
        "totals": totals,
    }


def country_tags(*, channel_meta: list[dict], target_country: dict[str, str]) -> list[str]:
    """Danh mục quốc gia có trong dữ liệu — gộp `channel_meta[].country` + values `target_country`.

    Bỏ rỗng, sort. Dùng cho dropdown lọc quốc gia của Viewer."""
    countries: set[str] = set()
    for c in channel_meta:
        x = c.get("country")
        if x:
            countries.add(x)
    for x in target_country.values():
        if x:
            countries.add(x)
    return sorted(countries)
