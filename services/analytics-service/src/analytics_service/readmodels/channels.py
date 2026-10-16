"""Kênh quản lý / đang hoạt động (THUẦN) — port từ `stats_core.summary` + `db._channel_breakdown`.

Đơn vị đếm: mỗi kênh là một đơn vị. Cờ `managed`/`active` do nguồn gửi có hiệu lực ghi đè; vắng thì suy ra:
managed = có cờ `active`; active = cờ `active` (mặc định chạy), kênh không `disabled`.
"""

from __future__ import annotations

from typing import NamedTuple

from analytics_service.readmodels.dataset import Dataset, Entity


class Unit(NamedTuple):
    managed: bool
    active: bool


def channel_units(ds: Dataset, e: Entity) -> list[Unit]:
    managed = e.managed_flag if e.managed_flag is not None else bool(e.active_flag)
    active = managed and (e.active_flag if e.active_flag is not None else True) and e.state != "disabled"
    return [Unit(managed, active)]
