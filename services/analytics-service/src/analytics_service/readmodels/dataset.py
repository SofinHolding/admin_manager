"""Dựng DATASET cho một truy vấn từ read model + cấu hình, và chọn tập kênh theo FilterContext.

Đây là đầu vào của MỌI phép tính (KPI, traffic, so sánh quốc gia, breakdown, compare): cùng một dataset, cùng một hàm
`select_entities` ⇒ KPI và biểu đồ không thể lệch bộ lọc.

Kênh (Entity) khoá theo `(owner, platform, external_id)`. Quy tắc quốc gia (giữ nguyên legacy, thêm project):
override của nhóm kênh > quốc gia của resource > quốc gia của project (liên kết đầu tiên có quốc gia); rồi alias → tên
chuẩn; rỗng = chưa biết (hiển thị "Khác").
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

import asyncpg

KHAC = "Khác"
ADMIN_ROLES = frozenset({"admin", "dash_admin"})

EntityKey = tuple  # (owner, platform, external_id)


@dataclass(frozen=True)
class Binding:
    project_key: str
    enabled: bool


@dataclass
class Entity:
    key: EntityKey
    owner: str
    platform: str
    ext_id: str
    name: str
    country: str                    # hiệu lực: sau override + alias; "" = chưa biết
    state: str
    bindings: list[Binding]
    managed_flag: bool | None
    active_flag: bool | None
    observed_at: datetime | None


@dataclass(frozen=True)
class ProjectInfo:
    key: str
    name: str
    enabled: bool
    country: str


@dataclass(frozen=True)
class ContentRow:
    owner: str
    platform: str
    resource: str
    date: str
    views: int | None
    reactions: int | None
    comments: int | None
    observed_at: datetime | None

    @property
    def ek(self) -> EntityKey:
        return (self.owner, self.platform, self.resource)


@dataclass(frozen=True)
class DailyRow:
    owner: str
    platform: str
    resource: str
    date: str
    views: int | None
    observed_at: datetime | None

    @property
    def ek(self) -> EntityKey:
        return (self.owner, self.platform, self.resource)


@dataclass(frozen=True)
class EventRow:
    owner: str
    platform: str
    resource: str
    kind: str
    date: str
    project_key: str

    @property
    def ek(self) -> EntityKey:
        return (self.owner, self.platform, self.resource)


@dataclass
class GroupInfo:
    id: str
    account_id: str
    name: str
    members: dict[str, dict] = field(default_factory=dict)   # channel_ref -> {platform_tag, country_override}


@dataclass
class Dataset:
    entities: dict[EntityKey, Entity]
    projects: dict[tuple[str, str], ProjectInfo]
    content: list[ContentRow]
    daily: list[DailyRow]
    events: list[EventRow]
    groups: dict[str, GroupInfo]
    aliases: dict[str, str]
    accounts: dict[str, dict]
    platforms: set[str]

    def project_of(self, entity: Entity, key: str) -> ProjectInfo | None:
        return self.projects.get((entity.owner, key))

    def group_owner_is_admin(self, account_id: str) -> bool:
        return (self.accounts.get(account_id) or {}).get("role") in ADMIN_ROLES

    def group_applies(self, group: GroupInfo, owner: str, ext_id: str) -> bool:
        """Nhóm áp lên kênh `(owner, ext_id)` khi kênh là thành viên VÀ nhóm thuộc đúng owner của kênh.

        `channel_ref` chỉ là external_id nên hai owner có thể trùng; nhóm của A không được tác động lên kênh của B (khi admin
        xem toàn bộ). Ngoại lệ có chủ đích: nhóm do admin/dash_admin tạo (theo `account_projection`) là nhóm quản trị,
        áp lên mọi owner như hành vi cũ."""
        return ext_id in group.members and (group.account_id == owner or self.group_owner_is_admin(group.account_id))


def normalize_country(raw: str | None, aliases: dict[str, str]) -> str:
    """Quốc gia thô -> tên chuẩn qua `country_aliases` (tra theo lower-case); không khớp thì giữ bản đã trim."""
    text = (raw or "").strip()
    if not text:
        return ""
    return aliases.get(text.lower(), text)


# ── Nạp dataset ─────────────────────────────────────────────────────────────────

async def load_dataset(conn: asyncpg.Connection, owner_scope: list[str] | None, window: tuple[str, str] | None = None,
                       *, with_rows: bool = True) -> Dataset:
    """Nạp dữ liệu của phạm vi owner. `window=(from,to)` giới hạn hàng content/metrics/events theo ngày (YYYY-MM-DD)."""
    aliases = {r["alias"]: r["canonical"] for r in await conn.fetch("SELECT alias, canonical FROM country_aliases")}

    prow = await conn.fetch(
        "SELECT owner_account_id, project_key, name, country, enabled FROM read_projects"
        " WHERE NOT removed AND ($1::text[] IS NULL OR owner_account_id = ANY($1))", owner_scope)
    projects = {(r["owner_account_id"], r["project_key"]): ProjectInfo(
        r["project_key"], r["name"] or r["project_key"], r["enabled"], normalize_country(r["country"], aliases))
        for r in prow}

    accounts = {r["account_id"]: dict(r) for r in await conn.fetch(
        "SELECT account_id, username, role, status, deleted FROM account_projection")}

    grows = await conn.fetch(
        "SELECT g.id, g.account_id, g.name, m.channel_ref, m.platform_tag, m.country_override"
        " FROM channel_groups g LEFT JOIN channel_group_members m ON m.group_id = g.id"
        " WHERE ($1::text[] IS NULL OR g.account_id = ANY($1)) ORDER BY m.created_at, m.channel_ref", owner_scope)
    groups: dict[str, GroupInfo] = {}
    overrides: dict[tuple[str, str], str] = {}     # (account_id của nhóm, channel_ref) -> quốc gia
    admin_overrides: dict[str, str] = {}           # nhóm của admin/dash_admin: áp lên mọi owner theo channel_ref
    for r in grows:
        g = groups.setdefault(r["id"], GroupInfo(r["id"], r["account_id"], r["name"]))
        if r["channel_ref"] is not None:
            g.members[r["channel_ref"]] = {"platform_tag": r["platform_tag"], "country_override": r["country_override"]}
            if r["country_override"]:
                value = normalize_country(r["country_override"], aliases)   # sau cùng thắng
                if (accounts.get(r["account_id"]) or {}).get("role") in ADMIN_ROLES:
                    admin_overrides[r["channel_ref"]] = value
                overrides[(r["account_id"], r["channel_ref"])] = value

    rrows = await conn.fetch(
        "SELECT owner_account_id, platform, resource_type, external_id, display_name, username, country, state, projects, managed,"
        " active, observed_at, removed FROM read_resources WHERE ($1::text[] IS NULL OR owner_account_id = ANY($1))"
        " ORDER BY owner_account_id, platform, external_id, resource_type", owner_scope)
    entities: dict[EntityKey, Entity] = {}
    removed_keys: set[EntityKey] = set()
    for r in rrows:
        key = (r["owner_account_id"], r["platform"], r["external_id"])
        if r["removed"]:
            removed_keys.add(key)
            continue
        if key in entities:     # hai resource_type cùng external_id: kênh hiển thị gộp một (giữ bản đầu theo thứ tự ổn định)
            continue
        bindings = [Binding(b["project_key"], b.get("enabled", True)) for b in (r["projects"] or [])]
        country = (overrides.get((r["owner_account_id"], r["external_id"])) or admin_overrides.get(r["external_id"])
                   or normalize_country(r["country"], aliases))
        if not country:
            for b in bindings:
                proj = projects.get((r["owner_account_id"], b.project_key))
                if proj and proj.country:
                    country = proj.country
                    break
        entities[key] = Entity(key, r["owner_account_id"], r["platform"], r["external_id"],
                               r["display_name"] or r["username"] or r["external_id"], country, r["state"], bindings,
                               r["managed"], r["active"], r["observed_at"])

    removed_keys -= set(entities)   # một resource_type bị gỡ không được che resource_type khác còn sống cùng external_id

    # Event có thể không mang `platform`: suy từ kênh cùng owner + external_id (nếu có).
    by_owner_ext: dict[tuple[str, str], str] = {}
    for (owner, platform, ext_id) in entities:
        by_owner_ext.setdefault((owner, ext_id), platform)

    content: list[ContentRow] = []
    daily: list[DailyRow] = []
    events: list[EventRow] = []
    if with_rows and window is not None:
        d0, d1 = date.fromisoformat(window[0]), date.fromisoformat(window[1])
        for r in await conn.fetch(
                "SELECT owner_account_id, platform, resource_external_id, published_date, views, reactions, comments,"
                " observed_at FROM read_content WHERE NOT removed AND state <> 'deleted'"
                " AND published_date BETWEEN $2 AND $3 AND ($1::text[] IS NULL OR owner_account_id = ANY($1))"
                " ORDER BY owner_account_id, platform, resource_external_id, published_date, external_id",
                owner_scope, d0, d1):
            key = (r["owner_account_id"], r["platform"], r["resource_external_id"])
            if key not in removed_keys:
                content.append(ContentRow(key[0], key[1], key[2], r["published_date"].isoformat(), r["views"],
                                          r["reactions"], r["comments"], r["observed_at"]))
        for r in await conn.fetch(
                "SELECT owner_account_id, platform, resource_external_id, metric_date, views, observed_at"
                " FROM read_daily_metrics WHERE NOT removed AND metric_date BETWEEN $2 AND $3"
                " AND ($1::text[] IS NULL OR owner_account_id = ANY($1))"
                " ORDER BY owner_account_id, platform, resource_external_id, metric_date", owner_scope, d0, d1):
            key = (r["owner_account_id"], r["platform"], r["resource_external_id"])
            if key not in removed_keys:
                daily.append(DailyRow(key[0], key[1], key[2], r["metric_date"].isoformat(), r["views"],
                                      r["observed_at"]))
        for r in await conn.fetch(
                "SELECT owner_account_id, platform, resource_external_id, kind, occurred_date, project_key"
                " FROM read_events WHERE NOT removed AND occurred_date BETWEEN $2 AND $3"
                " AND ($1::text[] IS NULL OR owner_account_id = ANY($1))"
                " ORDER BY owner_account_id, occurred_date, external_id", owner_scope, d0, d1):
            platform = r["platform"] or by_owner_ext.get((r["owner_account_id"], r["resource_external_id"]), "")
            key = (r["owner_account_id"], platform, r["resource_external_id"])
            if key not in removed_keys:
                events.append(EventRow(key[0], key[1], key[2], r["kind"], r["occurred_date"].isoformat(),
                                       r["project_key"]))

    return Dataset(entities, projects, content, daily, events, groups, aliases, accounts,
                   {e.platform for e in entities.values()})


# ── Chọn tập kênh theo FilterContext ────────────────────────────────────────────

@dataclass(frozen=True)
class Selection:
    """Kết quả áp FilterContext lên tập kênh.

    `keys=None`: không thu hẹp. Hàng số liệu của kênh CHƯA có resource (không nằm trong `known`) chỉ được giữ khi mọi bộ lọc
    đang bật đều đánh giá được từ chính hàng đó (nền tảng / quốc gia "Khác"): `unknown_natives` là các nền tảng bắt buộc
    khớp, hoặc None nếu có bộ lọc cần thông tin kênh (nhóm, nhãn, chủ đề, quốc gia cụ thể) ⇒ loại hàng mồ côi."""

    keys: frozenset | None
    native_scope: str           # nền tảng gốc đang lọc ("" nếu không)
    country: str                # quốc gia chuẩn hoá đã dùng để lọc ("" nếu không)
    known: Mapping | None = None
    unknown_natives: tuple | None = ()

    def allows(self, ek: EntityKey) -> bool:
        if self.keys is None or ek in self.keys:
            return True
        if self.known is not None and ek in self.known:
            return False
        if self.unknown_natives is None:
            return False
        return all(ek[1].lower() == n for n in self.unknown_natives)


def select_entities(ds: Dataset, ctx, *, ignore_country: bool = False) -> Selection:
    """Áp TOÀN BỘ FilterContext (trừ khoảng ngày, đã áp lúc nạp) lên tập kênh. Các điều kiện là AND."""
    chosen = set(ds.entities)
    narrowed = False
    needs_entity_info = False

    natives: list[str] = []
    free_tag = ctx.platform_tag
    if ctx.platform:
        natives.append(ctx.platform.lower())
    if ctx.platform_tag and ctx.platform_tag.lower() in {p.lower() for p in ds.platforms}:
        natives.append(ctx.platform_tag.lower())   # tương thích legacy: platform_tag = tên nền tảng gốc
        free_tag = ""
    for native in natives:
        chosen = {k for k in chosen if ds.entities[k].platform.lower() == native}
        narrowed = True
    native = natives[0] if natives else ""

    if free_tag:
        chosen = {k for k in chosen if any(
            ds.group_applies(g, ds.entities[k].owner, ds.entities[k].ext_id)
            and g.members[ds.entities[k].ext_id]["platform_tag"] == free_tag for g in ds.groups.values())}
        narrowed = needs_entity_info = True

    if ctx.group_id:
        group = ds.groups.get(ctx.group_id)   # chỉ có nhóm thuộc phạm vi owner (đã lọc lúc nạp)
        chosen = {k for k in chosen if group is not None
                  and ds.group_applies(group, ds.entities[k].owner, ds.entities[k].ext_id)}
        narrowed = needs_entity_info = True

    if ctx.project:
        chosen = {k for k in chosen if any(b.project_key == ctx.project for b in ds.entities[k].bindings)}
        narrowed = needs_entity_info = True

    country = ""
    if ctx.country and not ignore_country:
        country = normalize_country(ctx.country, ds.aliases)
        low = country.lower()
        if low == KHAC.lower():
            chosen = {k for k in chosen if not ds.entities[k].country}
        else:
            chosen = {k for k in chosen if ds.entities[k].country.lower() == low}
            needs_entity_info = True
        narrowed = True

    if not narrowed:
        return Selection(None, native, country)
    return Selection(frozenset(chosen), native, country, ds.entities,
                     None if needs_entity_info else tuple(dict.fromkeys(natives)))


def as_of(ds: Dataset, sel: Selection) -> datetime | None:
    """`observed_at` lớn nhất trong phạm vi lọc (kênh + hàng đã nạp)."""
    best: datetime | None = None
    for e in ds.entities.values():
        if sel.allows(e.key) and e.observed_at and (best is None or e.observed_at > best):
            best = e.observed_at
    for rows in (ds.content, ds.daily):
        for r in rows:
            if sel.allows(r.ek) and r.observed_at and (best is None or r.observed_at > best):
                best = r.observed_at
    return best
