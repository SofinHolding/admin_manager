import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class _Settings:
    FB_COOKIES: str = ""
    FB_DOC_ID: str = ""


def _load_cookie_value() -> str:
    cookie_value = os.environ.get("FB_COOKIES", "")
    if cookie_value:
        return cookie_value

    cookie_file = os.environ.get("FB_COOKIE_FILE", "")
    if not cookie_file:
        return ""

    path = Path(cookie_file)
    if not path.is_file():
        return ""

    return path.read_text(encoding="utf-8").strip()


def get_settings() -> _Settings:
    return _Settings(
        FB_COOKIES=_load_cookie_value(),
        FB_DOC_ID=os.environ.get("FB_DOC_ID", ""),
    )
