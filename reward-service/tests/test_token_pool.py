"""Test `engine/token_pool.py` — chọn token NGẪU NHIÊN xen kẽ, nhịp chung ngẫu nhiên, nhịp riêng từng token,
loại token, dừng khi đang chờ."""

from __future__ import annotations

import asyncio
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discord.gateway_session import GatewaySession  # noqa: E402
from engine.token_pool import TokenPool, TokenSlot  # noqa: E402


def _pool(n: int, *, delay_ms: int = 0, jitter_ms: int = 0, seed: int = 1) -> TokenPool:
    slots = [
        TokenSlot(token_id=i, label=f"t{i}", token=f"tok{i}",
                  session=GatewaySession(gateway_url="ws://127.0.0.1:1/", token=f"tok{i}"))
        for i in range(n)
    ]
    return TokenPool(slots, delay_ms=delay_ms, jitter_ms=jitter_ms, rng=random.Random(seed))


async def _draw(pool: TokenPool, times: int) -> list[int]:
    order = []
    for _ in range(times):
        slot = await pool.acquire(lambda: False)
        order.append(slot.token_id)
        pool.release(slot)
    return order


async def test_selection_is_random_but_never_repeats_same_token_back_to_back():
    order = await _draw(_pool(3), 300)
    assert all(a != b for a, b in zip(order, order[1:]))  # luôn xen kẽ
    counts = Counter(order)
    assert set(counts) == {0, 1, 2} and min(counts.values()) > 60  # dùng đều, không bị kẹt 1 token
    assert order[:6] != [0, 1, 2, 0, 1, 2]  # không phải vòng tròn cố định như hàng đợi


async def test_different_seeds_give_different_orders():
    assert await _draw(_pool(4, seed=1), 40) != await _draw(_pool(4, seed=2), 40)


async def test_single_token_is_reused_and_two_tokens_strictly_alternate():
    assert await _draw(_pool(1), 4) == [0, 0, 0, 0]
    order = await _draw(_pool(2), 6)
    assert order in ([0, 1, 0, 1, 0, 1], [1, 0, 1, 0, 1, 0])


async def test_disabled_token_is_skipped_and_exhaustion_returns_none():
    pool = _pool(2)
    a = await pool.acquire(lambda: False)
    pool.disable(a, "AUTH_INVALID")
    seen = set(await _draw(pool, 3))
    assert seen == {1 - a.token_id}
    pool.disable(pool.slots[1 - a.token_id], "x")
    assert not pool.has_usable()
    assert await pool.acquire(lambda: False) is None


async def test_global_gap_is_random_and_shrinks_with_more_tokens():
    # 4 token, delay 800 ⇒ spread = 200ms ⇒ khoảng cách giữa 2 lệnh liên tiếp ∈ [100, 300]ms (ngẫu nhiên).
    pool = _pool(4, delay_ms=800)
    stamps = []
    for _ in range(6):
        slot = await pool.acquire(lambda: False)
        stamps.append(time.monotonic())
        pool.release(slot)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(0.08 <= g <= 0.45 for g in gaps), gaps
    assert len({round(g, 2) for g in gaps}) > 1  # không đều nhau


async def test_each_token_keeps_its_own_delay_between_its_uses():
    # 2 token, delay 400ms: mỗi token phải cách lần dùng trước ≥ 400ms dù nhịp chung nhanh hơn nhiều.
    pool = _pool(2, delay_ms=400)
    used_at: dict[int, list[float]] = {0: [], 1: []}
    for _ in range(6):
        slot = await pool.acquire(lambda: False)
        used_at[slot.token_id].append(time.monotonic())
        pool.release(slot)
    for stamps in used_at.values():
        assert all(b - a >= 0.38 for a, b in zip(stamps, stamps[1:])), stamps


async def test_cooled_token_is_not_used_until_cooldown_ends():
    pool = _pool(2)
    a = await pool.acquire(lambda: False)
    pool.cool(a, 5.0)
    pool.release(a)
    assert set(await _draw(pool, 4)) == {1 - a.token_id}


async def test_acquire_wakes_up_on_stop_while_waiting():
    pool = _pool(1, delay_ms=60_000)
    slot = await pool.acquire(lambda: False)
    pool.release(slot)
    stop = {"v": False}

    async def _flip() -> None:
        await asyncio.sleep(0.3)
        stop["v"] = True

    flipper = asyncio.create_task(_flip())
    t0 = time.monotonic()
    assert await pool.acquire(lambda: stop["v"]) is None
    assert time.monotonic() - t0 < 2.0
    await flipper
