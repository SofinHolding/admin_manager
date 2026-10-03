"""Test thuần (không DB/mạng) cho các hàm khớp của `engine/reconcile.py`."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from engine.reconcile import _has_amount, snowflake_from_datetime  # noqa: E402


@pytest.mark.parametrize("text,point,expected", [
    ("\u2705 14,000 XP has been given to <@!981815742070669313>", 14000, True),
    ("\u2705 14.000 XP has been given to x", 14000, True),
    ("\u2705 14000 XP has been given to x", 14000, True),
    ("\u2705 114,000 XP has been given to x", 14000, False),   # số khác chứa 14000
    ("\u2705 1 XP has been given to x", 14000, False),
    ("\u2705 5 XP has been given to <@!1400014000140001400>", 14000, False),  # chuỗi số nằm trong ID mention
    ("\u2705 5 XP has been given to x", 5, True),
])
def test_amount_matching(text, point, expected):
    assert _has_amount(text, point) is expected


def test_snowflake_from_datetime_matches_discord_documented_example():
    # Discord docs: snowflake 175928847299117063 ↔ 2016-04-30 11:18:25.796 UTC
    sf = snowflake_from_datetime(datetime.datetime(2016, 4, 30, 11, 18, 25, 796000, tzinfo=datetime.timezone.utc))
    assert int(sf) >> 22 == 41944705796
