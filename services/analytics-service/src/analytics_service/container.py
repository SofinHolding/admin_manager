"""Các dependency dựng một lần ở `create_app` và gắn vào `app.state.container`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import Request
from manager_sdk import TokenVerifier

from analytics_service.cache import ResultCache
from analytics_service.config import Settings
from analytics_service.db import Database


@dataclass
class Container:
    settings: Settings
    db: Database
    cache: ResultCache
    verifier: TokenVerifier
    now: Callable[[], datetime]
    query: Any = None          # readmodels.query.QueryService
    consumer: Any = None       # feed_consumer.consumer.FeedConsumer | None
    extras: dict = field(default_factory=dict)


def get_container(request: Request) -> Container:
    return request.app.state.container
