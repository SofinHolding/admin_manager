"""Handbook (docs/handbook) không được mục rữa: link phải sống, mọi service/bảng trong registry phải được nhắc tới."""

from __future__ import annotations

import re
from pathlib import Path

from archlib import ROOT, load_registry  # noqa: F401  (archlib dùng chung của bộ test kiến trúc)

HANDBOOK = ROOT / "docs" / "handbook"
EXPECTED = ["README.md", "01-tong-quan-he-thong.md", "02-trang-thai-tinh-nang.md", "03-dich-vu.md",
            "04-du-lieu-va-su-kien.md", "05-xac-thuc-va-bao-mat.md", "06-huong-dan-phat-trien.md", "07-van-hanh-va-lo-trinh.md"]
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def broken_links(text: str, base: Path) -> list[str]:
    out = []
    for target in LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if not (base / target).resolve().exists():
            out.append(target)
    return out


def missing_names(text: str, names: list[str]) -> list[str]:
    return [n for n in names if n not in text]


def test_handbook_files_exist():
    assert [f for f in EXPECTED if not (HANDBOOK / f).is_file()] == []


def test_handbook_links_are_alive():
    bad = {f: broken_links((HANDBOOK / f).read_text(encoding="utf-8"), HANDBOOK) for f in EXPECTED}
    assert {f: b for f, b in bad.items() if b} == {}


def test_every_service_and_owned_table_is_documented():
    reg = load_registry()
    text = (HANDBOOK / "03-dich-vu.md").read_text(encoding="utf-8")
    services = list(reg["services"])
    assert missing_names(text, services) == []
    tables = [t for s in reg["services"].values() for t in s.get("owned_tables", []) if "*" not in t]
    # bảng trùng tên giữa service (feed_cursor, outbox...) chỉ cần được nhắc một lần; bảng riêng phải được nhắc
    assert missing_names(text, sorted(set(tables))) == []


def test_every_phase_row_of_claude_md_is_in_feature_status():
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    status = (HANDBOOK / "02-trang-thai-tinh-nang.md").read_text(encoding="utf-8")
    phases = re.findall(r"^\| ([0-9][0-9.–\-]*) \|", claude, flags=re.M)
    ids = [part for p in phases for part in re.split(r"[–\-]", p)]  # dòng gộp '1.4–1.7' ⇒ cả hai đầu phải có mặt
    assert ids and [i for i in ids if i not in status] == []


def test_checkers_detect_violations():
    """Test của test: bộ kiểm tra phải bắt được link hỏng và tên bị bỏ sót."""
    assert broken_links("[x](khong-co-file.md) [ok](README.md)", HANDBOOK) == ["khong-co-file.md"]
    assert missing_names("abc def", ["abc", "xyz"]) == ["xyz"]
