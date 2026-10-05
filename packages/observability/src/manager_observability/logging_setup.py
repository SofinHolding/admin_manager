"""Log JSON có cấu trúc: service_name, service_version, request_id, level, message."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from manager_observability.request_context import current_request_id

_STANDARD = {"name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
             "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
             "relativeCreated", "thread", "threadName", "processName", "process", "message",
             "taskName"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str, service_version: str) -> None:
        super().__init__()
        self._service = service_name
        self._version = service_version

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service_name": self._service,
            "service_version": self._version,
            "request_id": current_request_id(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(service_name: str, service_version: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name, service_version))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
