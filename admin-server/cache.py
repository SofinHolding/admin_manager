"""Cache Redis cho admin-server — Cache-Aside + Invalidation.

ĐỌC   : GET Redis → HIT trả luôn; MISS → PostgreSQL → SET Redis → trả.
INVALID: con dấu phiên bản (`Db.doc_version`) nằm TRONG khoá cache. Sync server ghi dữ liệu →
         con dấu đổi → mọi khoá cũ thành không-với-tới-được. Không cần DEL tường minh nên
         không có cửa sổ race "ghi xong mà DEL thất lạc".

Vì sao không invalidate tức thì mỗi lần ghi: `documents` bị mọi máy khách ghi liên tục, invalidate
tuyệt đối sẽ làm cache luôn rỗng. `CACHE_VERSION_TTL` là cửa sổ làm mới con dấu — cũng chính là độ
trễ tối đa của số liệu. Admin ép tổng hợp lại ngay bằng POST /v1/admin/cache/flush.

Redis chết KHÔNG được làm hỏng trang: mọi thao tác cache nuốt lỗi và rơi về đọc thẳng PostgreSQL.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger("admin.cache")

NS = "adm"
SCHEMA = "v1"          # tăng khi shape kết quả của stats_core đổi → mọi khoá cũ thành vô hiệu
VERSION_KEY = f"{NS}:{SCHEMA}:docver"


class Cache:
    """Bọc Redis asyncio — tự suy biến khi Redis không sẵn sàng hoặc chết giữa chừng."""

    def __init__(self, url: str, *, ttl: int, version_ttl: int, enabled: bool) -> None:
        self.ttl = ttl
        self.version_ttl = version_ttl
        self._url = url
        self._enabled = enabled and bool(url)
        self._r: Any = None

    @property
    def ready(self) -> bool:
        return self._r is not None

    async def connect(self) -> None:
        """Kết nối + PING. Hỏng → ghi cảnh báo và chạy tiếp KHÔNG cache."""
        if not self._enabled:
            logger.info("Cache Redis TAT (CACHE_ENABLED=0 hoac thieu REDIS_URL)")
            return
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]
            r = aioredis.from_url(
                self._url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await r.ping()
            self._r = r
            logger.info(
                "Cache Redis BAT — %s (ttl=%ds, cua so lam moi=%ds)",
                self._url, self.ttl, self.version_ttl,
            )
        except Exception as e:  # noqa: BLE001
            self._r = None
            logger.warning(
                "Khong ket noi duoc Redis (%s) — chay khong cache: %s",
                self._url, e,
            )

    async def close(self) -> None:
        r = self._r
        self._r = None
        if r is not None:
            try:
                await r.aclose()
            except Exception:  # noqa: BLE001
                pass

    # ── Low-level ───────────────────────────────────────────────────────────────

    async def get_raw(self, key: str) -> str | None:
        if self._r is None:
            return None
        try:
            return await self._r.get(key)
        except Exception as e:  # noqa: BLE001
            logger.debug("cache get_raw loi: %s", e)
            return None

    async def set_raw(self, key: str, value: str, ttl: int) -> None:
        if self._r is None:
            return
        try:
            await self._r.setex(key, ttl, value)
        except Exception as e:  # noqa: BLE001
            logger.debug("cache set_raw loi: %s", e)

    async def get_json(self, key: str) -> Any:
        raw = await self.get_raw(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception as e:  # noqa: BLE001
            logger.debug("cache get_json parse loi: %s", e)
            return None

    async def set_json(self, key: str, value: Any, ttl: int) -> None:
        try:
            raw = json.dumps(value, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            logger.debug("cache set_json encode loi: %s", e)
            return
        await self.set_raw(key, raw, ttl)

    async def invalidate_all(self) -> int:
        """Xoá mọi khoá `adm:*` kể cả con dấu phiên bản. SCAN theo lô, UNLINK — không dùng KEYS."""
        if self._r is None:
            return 0
        try:
            total = 0
            batch: list[str] = []
            async for key in self._r.scan_iter(match=f"{NS}:*", count=500):
                batch.append(key)
                if len(batch) >= 500:
                    await self._r.unlink(*batch)
                    total += len(batch)
                    batch = []
            if batch:
                await self._r.unlink(*batch)
                total += len(batch)
            return total
        except Exception as e:  # noqa: BLE001
            logger.debug("cache invalidate_all loi: %s", e)
            return 0


# ── Logging helper ──────────────────────────────────────────────────────────────

def _log_status(status: str, method: str, ms: float) -> None:
    logger.info("cache %s %s %.0fms", status, method, ms)


# ── CachedDb ────────────────────────────────────────────────────────────────────

class CachedDb:
    """Bọc `Db` — chỉ cache 5 truy vấn tổng hợp nặng của viewer.

    Mọi thuộc tính khác uỷ quyền thẳng cho `Db` gốc qua `__getattr__`: auth, invite key, quản lý
    tài khoản đi thẳng xuống CSDL, không qua cache. Có chủ ý — những bảng đó nhỏ, có index, và
    `last_login` đổi mỗi lần đăng nhập nên cache chúng vừa vô ích vừa thêm bề mặt sai lệch.
    """

    _CACHED = ("traffic_metric", "summary", "top_channels", "views_by_country", "country_tags")

    def __init__(self, inner: Any, cache: Cache) -> None:
        self._inner = inner
        self._cache = cache
        self._locks: dict[str, asyncio.Lock] = {}

    def __getattr__(self, name: str) -> Any:
        # Chặn tên gạch dưới để không đệ quy vô hạn khi `_inner` chưa gán xong.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.__dict__["_inner"], name)

    # ── Con dấu phiên bản ───────────────────────────────────────────────────────

    async def _version(self) -> str:
        """Con dấu phiên bản, làm mới nhiều nhất mỗi `version_ttl` giây.

        Đây là nơi duy nhất quyết định độ tươi. Không có ghi mới → con dấu không đổi → kết quả đã
        cache tiếp tục được dùng tới `ttl` (dài hơn `version_ttl` nhiều)."""
        v = await self._cache.get_raw(VERSION_KEY)
        if v:
            return v
        v = await self._inner.doc_version()
        await self._cache.set_raw(VERSION_KEY, v, self._cache.version_ttl)
        return v

    # ── Lõi Cache-Aside ─────────────────────────────────────────────────────────

    @staticmethod
    def _key(ver: str, method: str, params: tuple) -> str:
        raw = "|".join("" if p is None else str(p) for p in params)
        return f"{NS}:{SCHEMA}:{ver}:{method}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"

    async def _aside(self, method: str, params: tuple, compute: Any) -> Any:
        """GET Redis → HIT trả; MISS → compute() → SET Redis → trả.

        Gộp-một-lượt (single-flight): 6 request cùng khoá lúc cache nguội chỉ tính MỘT lần, số còn
        lại chờ rồi đọc kết quả. Không có nó thì mỗi lần con dấu đổi là một cơn bão tính lại."""
        if not self._cache.ready:
            _log_status("OFF", method, 0)
            return await compute()

        ver = await self._version()
        key = self._key(ver, method, params)

        hit = await self._cache.get_json(key)
        if hit is not None:
            _log_status("HIT", method, 0)
            return hit

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Kiểm lại sau khi giành khoá — tránh N worker cùng tính 1 lần
            hit = await self._cache.get_json(key)
            if hit is not None:
                _log_status("HIT", method, 0)
                return hit
            t0 = time.perf_counter()
            out = await compute()
            await self._cache.set_json(key, out, self._cache.ttl)
            _log_status("MISS", method, (time.perf_counter() - t0) * 1000)

        # Chặn trần để dict khoá không phình theo số tổ hợp bộ lọc. Xoá sạch cùng lắm làm mất tác
        # dụng gộp-một-lượt tạm thời, không sai kết quả.
        if len(self._locks) > 256:
            self._locks.clear()
        else:
            self._locks.pop(key, None)
        return out

    # ── 5 phương thức được cache ─────────────────────────────────────────────────

    async def traffic_metric(self, date_from: str, date_to: str,
                             scope: str = "", country: str = "") -> dict:
        return await self._aside(
            "traffic_metric", (date_from, date_to, scope, country),
            lambda: self._inner.traffic_metric(date_from, date_to, scope, country),
        )

    async def summary(self, date_from: str, date_to: str, country: str = "") -> dict:
        return await self._aside(
            "summary", (date_from, date_to, country),
            lambda: self._inner.summary(date_from, date_to, country),
        )

    async def top_channels(self, date_from: str, date_to: str, country: str = "",
                           limit: int = 15) -> list[dict]:
        return await self._aside(
            "top_channels", (date_from, date_to, country, limit),
            lambda: self._inner.top_channels(date_from, date_to, country, limit),
        )

    async def views_by_country(self, date_from: str, date_to: str) -> dict:
        return await self._aside(
            "views_by_country", (date_from, date_to),
            lambda: self._inner.views_by_country(date_from, date_to),
        )

    async def country_tags(self) -> list[str]:
        return await self._aside(
            "country_tags", (),
            lambda: self._inner.country_tags(),
        )
