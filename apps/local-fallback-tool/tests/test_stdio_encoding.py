"""Tiến trình con của Electron trên Windows có stdout/stderr mặc định cp1252: kết quả tiếng Việt từng làm sập `channels_db.py`
(UnicodeEncodeError 'charmap' ở ký tự 'ẻ') nên trang Kênh không tải được. Các điểm vào Python PHẢI luôn xuất UTF-8."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import channels_db  # noqa: E402
import system_api  # noqa: E402


class _Std:
    """Giả lập stdio của tiến trình con: stdout cp1252 (không mã hoá được tiếng Việt) như trên Windows tiếng Anh/Tây Âu."""

    def __init__(self, stdin_bytes: bytes = b"") -> None:
        self.out_buf, self.err_buf = io.BytesIO(), io.BytesIO()
        self.stdout = io.TextIOWrapper(self.out_buf, encoding="cp1252", write_through=True)
        self.stderr = io.TextIOWrapper(self.err_buf, encoding="cp1252", write_through=True)
        self.stdin = io.TextIOWrapper(io.BytesIO(stdin_bytes), encoding="cp1252")


def _run(monkeypatch, fn, cmd: dict | None = None) -> bytes:
    std = _Std(json.dumps(cmd).encode("utf-8") if cmd is not None else b"")
    monkeypatch.setattr(sys, "stdout", std.stdout)
    monkeypatch.setattr(sys, "stderr", std.stderr)
    monkeypatch.setattr(sys, "stdin", std.stdin)
    fn()
    std.stdout.flush()
    return std.out_buf.getvalue()


def test_channels_db_returns_vietnamese_as_utf8_even_when_stdout_is_cp1252(monkeypatch, tmp_path):
    monkeypatch.setattr(channels_db, "DB_PATH", str(tmp_path / "channels.db"))
    name = "Kênh Nguyễn Thị Tèo — Hà Nội ẻ"
    _run(monkeypatch, channels_db.main, {"action": "sync_shared", "users": [{"id": "u1", "username": "tèo"}], "channels": [
        {"id": "c1", "owner_account_id": "u1", "owner_username": "tèo", "platform": "facebook", "display_name": name,
         "input_ref": "profile:1"}]})
    out = _run(monkeypatch, channels_db.main, {"action": "list_channels"})
    rows = json.loads(out.decode("utf-8"))["channels"]
    assert rows[0]["display_name"] == name and rows[0]["owner_username"] == "tèo"


def test_system_api_cli_prints_vietnamese_as_utf8_even_when_stdout_is_cp1252(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["system_api.py", "catalog"])
    out = _run(monkeypatch, system_api.main)
    summaries = [e["summary"] for e in json.loads(out.decode("utf-8"))["endpoints"]]
    assert any("sống" in s for s in summaries)       # 'ố' không có trong cp1252


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_use_utf8_stdio_reconfigures_both_streams(monkeypatch, stream):
    std = _Std()
    monkeypatch.setattr(sys, "stdout", std.stdout)
    monkeypatch.setattr(sys, "stderr", std.stderr)
    system_api.use_utf8_stdio()
    getattr(sys, stream).write("Không lấy được số liệu")
    getattr(sys, stream).flush()
    buf = std.out_buf if stream == "stdout" else std.err_buf
    assert buf.getvalue().decode("utf-8") == "Không lấy được số liệu"
