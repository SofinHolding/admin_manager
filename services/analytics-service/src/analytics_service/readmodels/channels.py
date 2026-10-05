"""Kênh quản lý / đang hoạt động (THUẦN) — port từ `stats_core.summary` + `db._channel_breakdown`.

Đơn vị đếm: mỗi LIÊN KẾT (kênh, chủ đề) là một "cặp" (page chạy hai chủ đề = hai cặp); kênh không có chủ đề là một đơn vị
đơn (vd kênh video). Cờ `managed`/`active` do nguồn gửi có hiệu lực ghi đè; vắng thì suy ra:
managed = có >= 1 liên kết chủ đề; active = liên kết bật, chủ đề không tắt (chủ đề không tồn tại coi là chạy), kênh không
`disabled`.
"""

from __future__ import annotations

from typing import NamedTuple

from analytics_service.readmodels.dataset import Dataset, Entity


class Unit(NamedTuple):
    project_key: str | None
    managed: bool
    active: bool


def cho_chay(ds: Dataset, e: Entity, project_key: str) -> bool:
    """Chủ đề có cho cặp bên trong chạy không (mirror `stats_core.cho_chay`; chủ đề lạ -> chạy)."""
    proj = ds.projects.get((e.owner, project_key))
    return proj is None or proj.enabled


def channel_units(ds: Dataset, e: Entity) -> list[Unit]:
    if e.bindings:
        if e.managed_flag is False:
            return [Unit(None, False, False)]
        units = []
        for b in e.bindings:
            active = b.enabled and e.state != "disabled" and cho_chay(ds, e, b.project_key)
            if e.active_flag is False:
                active = False
            units.append(Unit(b.project_key, True, active))
        return units
    managed = e.managed_flag if e.managed_flag is not None else bool(e.active_flag)
    active = managed and (e.active_flag if e.active_flag is not None else True) and e.state != "disabled"
    return [Unit(None, managed, active)]
