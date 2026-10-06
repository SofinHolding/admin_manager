"""Chuẩn skeleton thư mục (docs/architecture/repo-layout-v1.md §3). Áp dụng cho `services/*` ngay từ bây giờ."""

from __future__ import annotations

from pathlib import Path

from archlib import ROOT, load_registry

SERVICES_DIR = ROOT / "services"
REQUIRED = ["README.md", "Dockerfile", ".env.example", "requirements.txt", "pytest.ini", "tests"]


def skeleton_problems(service_dir: Path, package: str, has_db: bool) -> list[str]:
    out = [f"thiếu {name}" for name in REQUIRED if not (service_dir / name).exists()]
    if not (service_dir / "src" / package / "main.py").is_file():
        out.append(f"thiếu src/{package}/main.py")
    migrations = (service_dir / "migrations").is_dir()
    if has_db and not migrations:
        out.append("service có DB nhưng thiếu migrations/")
    if not has_db and migrations:
        out.append("service không có DB nhưng có migrations/")
    return out


def test_every_service_dir_is_registered_and_follows_the_skeleton():
    reg = load_registry()["services"]
    by_dir = {Path(s["path"]).name: (n, s) for n, s in reg.items() if s["path"].startswith("services/")}
    on_disk = sorted(p.name for p in SERVICES_DIR.iterdir() if p.is_dir() and not p.name.startswith((".", "__")))
    assert [d for d in on_disk if d not in by_dir] == [], "thư mục trong services/ chưa khai ở service-registry.json"
    problems = {}
    for d in on_disk:
        name, svc = by_dir[d]
        found = skeleton_problems(SERVICES_DIR / d, svc["package"], svc.get("database") is not None)
        if found:
            problems[name] = found
    assert problems == {}


def test_env_examples_hold_no_real_secrets_and_use_the_service_prefix():
    reg = load_registry()["services"]
    for d in sorted(p for p in SERVICES_DIR.iterdir() if p.is_dir()):
        env = d / ".env.example"
        if not env.is_file():
            continue
        prefix = d.name.replace("-service", "").replace("api-", "").upper() + "_"
        keys = [ln.split("=", 1)[0].strip() for ln in env.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#") and "=" in ln]
        wrong = [k for k in keys if not (k.startswith(prefix) or k.startswith("API_GATEWAY_") or k in {"LOG_LEVEL"})]
        assert wrong == [], f"{d.name}: biến thiếu tiền tố {prefix}: {wrong}"
    assert reg  # registry đọc được


def test_skeleton_checker_detects_violations(tmp_path):
    """Test của test: thư mục thiếu file / sai migrations phải bị bắt."""
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "migrations").mkdir()
    found = skeleton_problems(tmp_path, "pkg", has_db=False)
    assert "service không có DB nhưng có migrations/" in found and "thiếu Dockerfile" in found and "thiếu src/pkg/main.py" in found
    assert "service có DB nhưng thiếu migrations/" in skeleton_problems(tmp_path / "src", "pkg", has_db=True)
